# Capability Flywheel

The flywheel is the domain layer that turns traces into measured capability
improvements. Most of it lives in
`packages/capability-compiler/src/flychain_capability_compiler/`, with API
orchestration in the gateway and background execution in the orchestrator.

## Core Object: CapabilitySpec

`CapabilitySpec` is the unit of improvement. It is defined in `schema.py` and
serialized as YAML or JSON.

Fields:

- `id`: globally meaningful slug, such as `groundedness`.
- `name`: display name.
- `description`: natural-language capability definition.
- `eval_dimensions`: weighted checks used by the judge.
- `slice_rules`: rules that decide whether a trace is in scope.
- `eligible_methods`: allowed training methods, currently enum values for SFT,
  DPO, KTO, and GRPO.
- `recipe_refs`: recipe YAML references.
- `promotion_gate`: threshold and regression tolerance.
- `metadata`: string metadata.

`EvalDimension` can reference a judge prompt Markdown file. If no prompt ref is
provided, the eval engine uses a generic judge template.

## Template Library

Templates are YAML specs loaded by `templates.py`. Packaged templates are
embedded in the Python package under `_assets/templates`, and source templates
also live in `capabilities/templates`.

Current templates:

- `groundedness`
- `instruction-following`
- `code-correctness`
- `uncertainty-calibration`
- `multi-step-reasoning`

`GET /v1/capabilities/templates` exposes these to the dashboard.

## Capability Compiler

`compiler.py` implements the natural-language path:

1. `propose_questions(description)` asks an LLM for 3-6 clarifying questions.
2. The dashboard collects free-text answers.
3. `compile(description, answers)` asks the LLM for strict JSON.
4. `_coerce_spec` normalizes loose LLM output into a validated
   `CapabilitySpec`.

The compiler uses the `LLMClient` protocol from `llm.py`. `auto_client` chooses:

1. OpenAI if `OPENAI_API_KEY` is set or preferred.
2. Anthropic if `ANTHROPIC_API_KEY` is set or preferred.
3. Local Ollama otherwise.

Gateway compiler endpoints set the Ollama model from local runtime settings.

## Eval Engine

`eval.py` owns evaluation:

1. Wrap input/output/context/tags in `TraceData`.
2. Use `SliceMatcher` to decide whether the trace belongs to a capability.
3. For each eval dimension, load and render the judge template.
4. Split rendered Markdown into system and user sections.
5. Call the judge LLM in JSON mode where supported.
6. Parse `{ "score": ..., "passed": ..., "reason": ... }`.
7. Return `EvalScore` rows.

`aggregate_score` computes a weighted mean over a capability's eval dimensions.

### Slice Rule Policy

Current slice matching behavior:

- No rules means every trace matches.
- `tag` rules support `key=value` or key-presence checks.
- `regex` rules search over input and output.
- `semantic` rules are advisory only in current code.
- If a capability has only semantic rules, every trace matches.
- If concrete rules exist, semantic rules do not widen the match.
- Negated concrete rules invert the individual rule hit.

## Judge Prompt Templates

Judge prompts live in `evals/judge-prompts`. A template uses:

```markdown
## System

...

## User

Prompt: {{ trace.input }}
Context: {{ trace.context }}
Output: {{ trace.output }}
```

The renderer substitutes `trace.input`, `trace.output`, and `trace.context`.
If a referenced template is missing, the engine falls back to a generic prompt.

## Failure Derivation

Failures are not a separate table today. The gateway derives them by combining:

- Eval score rows where at least one dimension did not pass.
- Trace request/response payloads from `TraceStore`.
- Latest feedback per trace, especially `corrected_response`.
- Capability weights to compute an aggregate score.

This derivation powers dashboard failure inventory, clustering by failure IDs,
and dataset synthesis from stored clusters.

## Clustering

`cluster.py` implements failure clustering.

Input is a list of `FailedTrace` objects. Each failure produces a signature
from prompt, failing output, optional context, and optional ideal response.

Flow:

1. Embed signatures with an `Embedder`.
2. Convert to a NumPy matrix.
3. If there are fewer rows than `min_cluster_size`, return a provisional
   `insufficient data` cluster.
4. Run scikit-learn `HDBSCAN`.
5. Treat label `-1` as noise.
6. Optionally label each cluster with an LLM summarizer.
7. Return `ClusteringResult`.

`auto_embedder` currently chooses local Ollama unless `FLYCHAIN_EMBEDDER=hash`
is set. `HashEmbedder` is deterministic and useful for tests, not semantic
production embedding.

## Dataset Synthesis

Dataset synthesis is cluster-scoped.

SFT rows:

- Use `corrected_response` as the ideal response when available.
- Optionally generate an ideal response when missing.
- Write rows with `messages`, `prompt`, `completion`, `capability_id`, and
  `cluster_id`.

DPO rows:

- Use `corrected_response` or generated ideal as `chosen`.
- Use the original failing output as `rejected`.
- Skip rows where chosen is missing or identical to rejected.

The gateway writes JSONL files and records dataset metadata in the dataset
index.

## Recipes

`recipe.py` defines training recipe schema.

Fields:

- `id`
- `base_model`
- `method`
- `backend`
- `hyperparams`
- `eval_suite_ref`
- `promotion_threshold`
- `max_other_regression`
- `description`

Packaged recipes are loaded from `_assets/recipes`; source YAML lives in
`recipes/`.

Current shipped recipes:

- `sft-mlx-lora`
- `sft-unsloth-lora`
- `dpo-mlx-lora`
- `dpo-unsloth-lora`

## Training Backends

`training.py` defines `TrainingBackend` and registered implementations:

- `DryRunBackend`: always available, writes a fake adapter and train log.
- `MLXLMBackend`: wraps `mlx_lm.lora` or `mlx_lm.dpo` on Darwin hosts with
  `mlx_lm` importable.
- `UnslothBackend`: creates and runs a Python driver on Linux CUDA hosts with
  `nvidia-smi` and `unsloth` available.

Backend selection:

- `get_backend(name)` requires an exact registered backend.
- `select_backend(recipe_backend, allow_fallback=True)` uses the recipe backend
  if available, otherwise falls back to dry-run when allowed.
- `auto_host_backend()` reports host preference but gateway training run
  creation uses the recipe backend.

## Promotion Gate

`gate.py` implements `apply_gate`.

Inputs:

- Target capability ID.
- Baseline score map.
- Candidate score map.
- Target threshold.
- Maximum tolerated regression for other capabilities.

Decision rules:

1. Archive if target score is missing.
2. Archive if target delta is below threshold.
3. Archive if any non-target capability delta is lower than
   `-max_other_regression`.
4. Promote otherwise.

The verdict records all deltas, regressions, decision, threshold, and reason.

## Orchestrated Training And Gate Flow

The gateway owns API validation and run creation. The orchestrator owns
background execution.

1. Gateway validates capability, recipe, and dataset.
2. Gateway writes a queued `TrainingRun`.
3. Gateway enqueues `run_training_recipe`.
4. Orchestrator loads the run from the shared data dir.
5. Orchestrator selects and runs the backend.
6. Orchestrator saves artifact metadata and marks the run `trained`.
7. Gateway can run A/B comparison and store latest comparison on the run.
8. Gateway enqueues `apply_promotion_gate`.
9. Orchestrator applies the gate and writes an active adapter pointer if
   promoted.

## Current Gaps In The Flywheel

- Cluster scheduling is manual/API-driven, not periodic.
- The ClickHouse `failure_embeddings` table exists but clustering currently
  stores cluster results in JSON, not embeddings in ClickHouse.
- Active adapter pointers are persisted but not automatically loaded into
  serving.
- KTO and GRPO are schema-level enum options but not implemented training
  paths.
