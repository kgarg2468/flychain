# Persistence And Configuration

FlyChain's current local-first persistence combines ClickHouse, Redis, Ollama,
and a shared filesystem data directory. Postgres is provisioned for future
metadata storage but is not used by current application code.

## Docker Compose Services

`docker-compose.yml` defines:

- `clickhouse`: trace/eval/feedback database.
- `postgres`: provisioned metadata database, currently unused by code.
- `redis`: arq job queue.
- `ollama`: local LLM and embedding service.
- `gateway`: FastAPI API server.
- `orchestrator`: arq worker.
- `dashboard`: Next.js app.

Gateway and orchestrator share `./.flychain-data:/data`, and Compose sets
`FLYCHAIN_DATA_DIR=/data` for both.

## ClickHouse

Schema lives in `infra/clickhouse/init/001_schema.sql`.

### `traces`

Stores raw gateway proxy records:

- IDs: `trace_id`, `span_id`, `parent_span_id`
- Routing: `project_id`, `capability_ids`, `provider`, `model`, `method`
- Payloads: `request`, `response`
- Metrics: token counts, cost, latency
- Status: `status`, `error`
- Metadata: `tags`, `ts`

The table uses MergeTree, monthly partitions, and a 180-day TTL.

### `eval_scores`

Stores per-trace, per-capability, per-dimension judge scores:

- `trace_id`
- `project_id`
- `capability_id`
- `dimension`
- `score`
- `passed`
- `reason`
- `judge_model`
- `ts`

### `failure_embeddings`

Reserved for failed trace embeddings. The table exists, but current clustering
does not write embeddings here; it computes embeddings in request flow and
persists cluster results to JSON.

### `feedback`

Stores user feedback tied to traces:

- `feedback_id`
- `trace_id`
- `project_id`
- `score`
- `thumb`
- `comment`
- `corrected_response`
- `ts`

## TraceStore Fallback Behavior

`TraceStore` tries to connect to ClickHouse. If ClickHouse is unavailable, it
logs a warning and uses a process-local in-memory buffer.

Writes always append to the buffer first, then attempt to flush to ClickHouse.
If a flush fails, rows are returned to the buffer. Reads merge persisted rows
with buffered rows and deduplicate by stable keys.

This behavior keeps tests and laptop workflows usable without the full stack,
but buffered rows are not durable and are not shared across processes.

## File-Backed Data Directory

Default data dir:

- `FLYCHAIN_DATA_DIR` when set.
- Otherwise `~/.flychain/data`.

Compose uses `/data`, bind-mounted from `./.flychain-data`.

Current layout:

```text
$FLYCHAIN_DATA_DIR/
  capabilities/
    <capability_id>.yaml
  clusters/
    <capability_id>.json
  datasets/
    index.json
    <capability_id>/
      <dataset_id>.jsonl
  runs/
    <run_id>.json
    <run_id>/
      artifacts/
        adapter/
        train.log
  pointers/
    <capability_id>.json
  replay-sets/
    <replay_set_id>.json
  settings.json
```

### CapabilityStore

Stores each `CapabilitySpec` as YAML. Users can inspect and edit these files
directly, but API writes do not merge concurrent edits.

### ClusterStore And DatasetStore

`ClusterStore` writes one JSON clustering result per capability. `DatasetStore`
records dataset metadata in `datasets/index.json` and resolves dataset IDs to
JSONL paths.

### TrainingRunStore And AdapterPointerStore

`TrainingRunStore` writes one run JSON file per run. Run directories also hold
backend artifacts.

`AdapterPointerStore` writes one JSON pointer per capability when a run is made
active or promoted by the gate.

### ReplaySetStore

Replay sets are JSON files containing named rows with baseline and candidate
outputs. A/B comparison can evaluate inline replay rows or a stored replay set.

### SettingsStore

Local non-secret runtime settings live in `settings.json`:

- `judge_model`
- `embedding_model`
- `min_cluster_size`
- `auto_eval_new_traces`
- `auto_cluster_failures`

`auto_cluster_failures` is stored and editable but not wired to scheduled
clustering in current code.

## Redis And arq

Redis is used as the arq queue. Gateway enqueues:

- `evaluate_trace`
- `run_training_recipe`
- `apply_promotion_gate`

Orchestrator registers these functions in `WorkerSettings`.

If Redis is not reachable during gateway startup, `job_queue` is `None`.
Auto-eval logs and skips queueing; explicit training/gate queue routes return
`503`.

## Ollama

Ollama is used for three current roles:

- Local OpenAI-compatible chat provider through the gateway's `local-ollama`
  route.
- Default compiler/eval/dataset-generation judge through `/api/chat`.
- Default embedding service through `/api/embeddings`.

The CLI bootstrap command pulls:

- `llama3.2:3b`
- `nomic-embed-text`

## Postgres

Postgres is started by Compose and its URL is exposed as `FLYCHAIN_POSTGRES_URL`
to gateway and orchestrator. Current code loads the setting but does not create
Postgres clients or write metadata there.

When metadata moves out of JSON files in the future, Postgres is the likely
target, but that is not implemented today.

## Environment Precedence

Pydantic settings read from process environment and `.env` with `FLYCHAIN_`
prefix. The gateway also writes some settings back into `os.environ` during
startup so the compiler package can use the same data dir, model registry,
template dir, recipe dir, and embedding model.

Important override paths:

- `FLYCHAIN_MODELS_YAML`
- `FLYCHAIN_TEMPLATES_DIR`
- `FLYCHAIN_RECIPES_DIR`
- `FLYCHAIN_DATA_DIR`

Optional provider keys:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`

These are not stored by the dashboard settings page.

## Compose Networking Details

Inside Compose:

- Gateway reaches ClickHouse at `http://flychain:flychain@clickhouse:8123/flychain`.
- Gateway reaches Redis at `redis://redis:6379/0`.
- Gateway reaches Ollama at `http://ollama:11434`.
- Dashboard server reaches gateway at `http://gateway:8080`.
- Browser-visible gateway URL is `http://localhost:8080`.

Outside Compose, defaults point at `localhost` ports.
