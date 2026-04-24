# Gateway Deep Dive

The gateway is the current API and control-plane center of FlyChain. It is a
FastAPI app in `apps/gateway/src/flychain_gateway/main.py`.

## Startup

Gateway startup happens in the FastAPI lifespan function:

1. Load `Settings` from environment and `.env`.
2. Export selected settings back to process env so the compiler package can
   locate data dirs, templates, recipes, model registry, and Ollama models.
3. Configure OpenTelemetry tracing.
4. Load `ModelRegistry` from packaged `models.yaml` or `FLYCHAIN_MODELS_YAML`.
5. Create `TraceStore` and connect to ClickHouse if available.
6. Create `ProviderRouter`.
7. Create file-backed stores rooted at `default_data_dir()`.
8. Try to create an arq Redis pool. If Redis is unavailable, background jobs are
   disabled and queue-dependent routes return `503`.

The stores are attached to `app.state` and reused by route handlers.

## Settings

`apps/gateway/src/flychain_gateway/config.py` defines gateway settings with
`FLYCHAIN_` env prefix:

- `env`
- `data_dir`
- `clickhouse_url`
- `postgres_url`
- `redis_url`
- `ollama_url`
- `judge_model`
- `embedding_model`
- `models_yaml`
- `templates_dir`
- `recipes_dir`
- `openai_base_url`
- `openai_api_key`
- `anthropic_base_url`
- `anthropic_api_key`
- `otlp_endpoint`
- `default_project_id`

`settings_store.py` separately stores non-secret runtime knobs in
`$FLYCHAIN_DATA_DIR/settings.json`: judge model, embedding model, min cluster
size, and auto-eval/auto-cluster toggles.

## Provider Routing

`ModelRegistry` indexes provider models from YAML:

- `openai`
- `anthropic`
- `local-ollama`

Each model can be referenced by raw ID, such as `gpt-4o-mini`, or by
namespaced ID, such as `openai:gpt-4o-mini`.

`ProviderRouter` creates three adapters:

- `OpenAICompatibleProvider("openai")`
- `OpenAICompatibleProvider("local-ollama")`
- `AnthropicProvider`

Routing rules:

- `resolve_chat` accepts `openai` and `local-ollama` models for
  `/v1/chat/completions`.
- `resolve_messages` accepts only `anthropic` models for `/v1/messages`.
- Anthropic models on the chat route, or OpenAI/Ollama models on the messages
  route, are rejected with an explanatory error.

Provider adapters are intentionally transport-thin. They forward the request
body, parse JSON, extract usage, and report upstream errors as structured
`ProviderResponse` objects.

## Headers And Trace Metadata

Proxy routes parse:

- `x-flychain-project`: project ID. Defaults to `Settings.default_project_id`.
- `x-flychain-capabilities`: comma-separated capability IDs used for optional
  auto-eval filtering.
- `x-flychain-tags`: comma-separated `key=value` pairs stored on the trace.

The CLI instrumentation code currently injects `x-flychain-project` and
informational tag headers named `x-flychain-tags-<key>`. The gateway parser
expects the compact `x-flychain-tags` format, so tag propagation from CLI
instrumentation is an area to review before relying on it for production-like
traffic slices.

## Endpoint Groups

### Health And Registry

- `GET /healthz`
- `GET /version`
- `GET /v1/models`

### Provider Proxy

- `POST /v1/chat/completions`
- `POST /v1/messages`

Both proxy routes:

1. Reject streaming requests.
2. Resolve the model to a provider adapter.
3. Rewrite `model` to the provider's concrete model ID.
4. Call the provider.
5. Emit OpenTelemetry LLM attributes.
6. Write a trace.
7. Optionally enqueue auto-eval.
8. Return provider payload with `x-flychain-trace-id`.

### Feedback

- `POST /v1/feedback`

Feedback rows support thumb, numeric score, comment, and corrected response.
Corrected responses feed later dataset synthesis.

### Capabilities And Compiler

- `GET /v1/capabilities/templates`
- `GET /v1/capabilities`
- `GET /v1/capabilities/{capability_id}`
- `POST /v1/capabilities/from-template`
- `POST /v1/capabilities`
- `DELETE /v1/capabilities/{capability_id}`
- `POST /v1/capabilities/compiler/questions`
- `POST /v1/capabilities/compiler/compile`

The compiler endpoints call `CapabilityCompiler` with `auto_client`, using the
runtime judge model. Compile output is not persisted until the client posts the
full spec to `/v1/capabilities`.

### Eval, Traces, Scorecards, And Failures

- `POST /v1/eval`
- `GET /v1/traces`
- `GET /v1/capabilities/{capability_id}/scorecard`
- `GET /v1/capabilities/{capability_id}/failures`
- Debug helpers: `GET /debug/traces`, `GET /debug/feedback`,
  `GET /debug/eval-scores`

`/v1/eval` creates a `TraceData`, evaluates against explicit capability IDs or
all persisted capabilities, writes scores, and returns per-capability results.

`/v1/traces` reads trace rows and can filter by project, capability, status, and
provider. Capability filtering is implemented by finding trace IDs with eval
scores for that capability.

`/failures` derives current failing rows by grouping eval scores, selecting
dimensions where `passed` is false, joining trace payloads, and attaching latest
feedback.

### Clusters And Datasets

- `POST /v1/capabilities/{capability_id}/cluster-run`
- `GET /v1/capabilities/{capability_id}/clusters`
- `POST /v1/capabilities/{capability_id}/synthesize-dataset`
- `GET /v1/capabilities/{capability_id}/datasets`

Cluster runs accept inline failures or failure IDs. Dataset synthesis accepts an
inline cluster or stored cluster ID.

### Recipes And Training Runs

- `GET /v1/recipes`
- `GET /v1/recipes/{recipe_id}`
- `POST /v1/training-runs`
- `GET /v1/training-runs`
- `GET /v1/training-runs/{run_id}`
- `POST /v1/training-runs/{run_id}/apply-gate`

Creating a training run validates capability, recipe, and dataset, saves a
queued run, then enqueues `run_training_recipe`. Applying a gate requires a
trained, archived, or promoted run and enqueues `apply_promotion_gate`.

### Replay Sets, A/B Compare, And Adapter Pointers

- `GET /v1/capabilities/{capability_id}/replay-sets`
- `POST /v1/capabilities/{capability_id}/replay-sets`
- `PUT /v1/capabilities/{capability_id}/replay-sets/{replay_set_id}`
- `POST /v1/capabilities/{capability_id}/ab-compare`
- `GET /v1/capabilities/{capability_id}/active-adapter`
- `POST /v1/capabilities/{capability_id}/active-adapter`
- `DELETE /v1/capabilities/{capability_id}/active-adapter`

A/B comparison evaluates baseline and candidate outputs from replay rows and
can persist the latest comparison onto a training run. Adapter activation
requires a trained or promoted run with an artifact.

## Stores

| Store                 | File                  | Backing                                |
| --------------------- | --------------------- | -------------------------------------- |
| `TraceStore`          | `trace_store.py`      | ClickHouse with in-memory fallback     |
| `CapabilityStore`     | `capability_store.py` | YAML files                             |
| `ClusterStore`        | `cluster_store.py`    | JSON per capability                    |
| `DatasetStore`        | `cluster_store.py`    | JSONL files plus `datasets/index.json` |
| `TrainingRunStore`    | `training_store.py`   | JSON per run                           |
| `AdapterPointerStore` | `training_store.py`   | JSON per capability                    |
| `ReplaySetStore`      | `replay_store.py`     | JSON per replay set                    |
| `SettingsStore`       | `settings_store.py`   | Single JSON settings file              |

## Observability

`otel.py` builds OpenInference-style attributes:

- `openinference.span.kind`
- `llm.provider`
- `llm.model_name`
- `llm.invocation_type`
- token counts
- latency
- bounded serialized input/output payloads
- `flychain.project_id`

The tracer provider is idempotently configured. Without `FLYCHAIN_OTLP_ENDPOINT`,
spans exist for instrumentation and tests but are not exported.

## Queue Handoff

Gateway creates an arq Redis pool at startup. Queue usage:

- Auto-eval enqueues `evaluate_trace`.
- Training run creation enqueues `run_training_recipe`.
- Gate application enqueues `apply_promotion_gate`.

If Redis is unavailable, auto-eval is skipped with a warning and explicit
queue-dependent routes return `503`.

## Failure Modes To Know

- ClickHouse unavailable: reads and writes use a process-local memory buffer.
- Redis unavailable: background jobs are disabled.
- Provider upstream error: gateway writes an error trace and returns `502`.
- Streaming requested: gateway returns `400`.
- Unknown model or wrong route for provider: gateway returns `404`.
- Dataset ID missing: training run creation returns `404`.
- Gate applied before eligible run state: gateway returns `409`.
