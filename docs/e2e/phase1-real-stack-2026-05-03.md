# Phase 1 Real-Stack E2E Evidence - 2026-05-03

Status: passed

## Environment

- Repo: `/Users/krishgarg/Documents/Projects/flychain`
- Runtime data dir: `/Users/krishgarg/Documents/Projects/flychain/.flychain-phase1-e2e-20260503T002025`
- Capability id: `phase1-exact-sentinel-2026-05-03`
- Project/tag: `phase1-e2e-2026-05-03`
- Gateway: `http://localhost:8080`
- Dashboard: `http://localhost:3000`
- MLX server: `http://127.0.0.1:8081`

## Automated Checks

Fresh verification run on 2026-05-03:

| Command | Result |
| --- | --- |
| `uv run pytest` | Passed: 148 passed, 5 sklearn warnings |
| `pnpm -r --if-present test` | Passed: dashboard 18, CLI 24, SDK TS 4 |
| `pnpm -r --if-present typecheck` | Passed |
| `uv run ruff check apps packages` | Passed: all checks passed |
| `uv run mypy apps packages` | Passed: no issues in 35 source files |

## Service Readiness

- Docker infra started with `docker compose up -d clickhouse postgres redis ollama`.
- Ollama models pulled in `flychain-ollama`: `llama3.2:3b`, `nomic-embed-text`.
- Gateway host process running on `:8080`.
- Orchestrator host worker running and connected to Redis.
- Dashboard dev server running on `:3000`.
- `GET /healthz`: `{"status":"ok"}`.
- `GET /v1/settings`: runtime shows local Ollama, Redis, ClickHouse, data dir,
  and `mlx_server_url=http://127.0.0.1:8081`.
- `GET /v1/jobs`: reachable and initially empty.
- `GET http://localhost:3000`: HTTP 200.

## Sentinel Capability And Eval

- Created capability `phase1-exact-sentinel-2026-05-03`.
- Exact-match evaluator:
  - expected output: `PHASE1_SENTINEL_OK`
  - mode: `deterministic`
  - type: `exact_match`
  - trim normalization: `false`
  - whitespace collapse: `false`
- Enabled runtime settings:
  - `auto_eval_new_traces=true`
  - `auto_cluster_failures=true`
- Baseline chat traces generated through `/v1/chat/completions` with provider
  `local-ollama`, model `llama3.2:3b`, output `PHASE1_SENTINEL_BAD`:
  - `trace_01KQPBEQKBBR0C14HT696GS7RB`
  - `trace_01KQPBETN9VJQ2XEZJD81S8JJG`
  - `trace_01KQPBEXM8E8PHE3ZKA3YN72FR`
- Auto-eval jobs succeeded for all three traces.
- Persisted eval scores for all three baseline traces:
  - `score=0.0`
  - `passed=false`
  - `reason="expected exact match 'PHASE1_SENTINEL_OK'"`
  - `judge_model=deterministic:exact_match`
  - `evaluator_type=deterministic`
  - `evaluator_source=deterministic:exact_match`
- Failure API returned all three traces with prompt, wrong output,
  `failing_dimensions=["exact_sentinel"]`, and no correction initially.
- Added feedback corrections of exactly `PHASE1_SENTINEL_OK`.
- Failure API then returned `corrected_response="PHASE1_SENTINEL_OK"` for all
  three failures.
- Auto-cluster persisted one cluster:
  - id: `phase1-exact-sentinel-2026-05-03-c0`
  - size: `3`
  - trace ids: the three failing baseline traces.

## Dataset And Training

- Synthesized SFT dataset from the corrected cluster with `generate_missing=false`.
- Dataset:
  - id: `ds_01KQPBGB1E46YP5A3MQPW97GSB`
  - row count: `3`
  - path: `.flychain-phase1-e2e-20260503T002025/datasets/phase1-exact-sentinel-2026-05-03/ds_01KQPBGB1E46YP5A3MQPW97GSB.jsonl`
- Verified dataset rows contain assistant completion `PHASE1_SENTINEL_OK`.
- Queued training with:
  - recipe: `sft-mlx-lora-local-3b`
  - baseline: `0.0`
  - `allow_backend_fallback=false`
- Training run:
  - id: `run_01KQPBGSGJMW85AYGPRP5DVTV8`
  - job id: `job_01KQPBGSGJMW85AYGPRP5DVTV9`
  - job status: `succeeded`
  - duration: `61557ms`
- Artifact proof:
  - `backend=mlx-lm`
  - `dry_run=false`
  - `base_model=mlx-community/Llama-3.2-3B-Instruct-4bit`
  - adapter dir: `.flychain-phase1-e2e-20260503T002025/runs/run_01KQPBGSGJMW85AYGPRP5DVTV8/artifacts/adapter`
- Train log showed `mlx_lm.lora`, 300 iterations, and adapter weights saved.

## Served Validation, Retry, And Promotion

- Before served validation:
  - Manual activation returned HTTP `409`.
  - Active adapter pointer was `null`.
  - Promotion gate archived the run with reason:
    `served validation proof is incomplete...`.
  - Active adapter pointer remained `null`.
- Created replay set:
  - id: `replay_01KQPBMB80K6A42QAB9AH6DB82`
  - expected candidate output: `PHASE1_SENTINEL_OK`
  - sample count: `3`
- Queued served validation while MLX server was intentionally offline.
- Controlled failure proof:
  - job id: `job_01KQPBMHTEQKKAGTKGQXZZ6ZHG`
  - initial status: `failed`
  - retry count: `0`
  - max retries: `1`
  - timeout: `300s`
  - duration: `23ms`
  - error: `502 Bad Gateway` from the internal chat-completions call.
- Dashboard/API evidence:
  - `/v1/jobs` exposed job type, failed status, timestamps, retry count,
    timeout, error, and retry payload.
  - Dashboard HTML contained the capability, active adapter state, and jobs
    including `served_validation`.
- Started real MLX server on `127.0.0.1:8081` with the trained adapter.
- Retried the failed served-validation job.
- Retry proof:
  - retry endpoint changed status to `retrying`
  - retry count became `1`
  - same job later became `succeeded`.
- Served validation result:
  - `status=passed`
  - `aggregate_score=1.0`
  - `sample_count=3`
  - validation trace ids:
    - `trace_01KQPBN937KNB862WG08CXZP9P`
    - `trace_01KQPBNA3GTP34YC0H16NFP8VT`
    - `trace_01KQPBNACSF2YV224RGHCKH0F1`
  - `provider=local-mlx`
  - `model=mlx-community/Llama-3.2-3B-Instruct-4bit`
  - `adapter_run_id=run_01KQPBGSGJMW85AYGPRP5DVTV8`
  - `adapter_capability_id=phase1-exact-sentinel-2026-05-03`
  - `routing_mode=candidate`
  - outputs: three `PHASE1_SENTINEL_OK`
  - failures: `[]`
- Applied promotion gate after served validation.
- Final promotion proof:
  - run status: `promoted`
  - gate decision: `promote`
  - target delta: `1.0`
  - threshold: `0.05`
  - no regressions
  - active pointer run id: `run_01KQPBGSGJMW85AYGPRP5DVTV8`
- Active chat proof:
  - request used normal `/v1/chat/completions` with only capability header.
  - response output: `PHASE1_SENTINEL_OK`
  - trace id: `trace_01KQPBPDZMTDRHZHM00FWR350V`
  - response headers:
    - `x-flychain-provider=local-mlx`
    - `x-flychain-model=mlx-community/Llama-3.2-3B-Instruct-4bit`
    - `x-flychain-adapter-run-id=run_01KQPBGSGJMW85AYGPRP5DVTV8`
    - `x-flychain-adapter-capability-id=phase1-exact-sentinel-2026-05-03`
    - `x-flychain-adapter-routing-mode=active`
    - `x-flychain-active-adapter-run-id=run_01KQPBGSGJMW85AYGPRP5DVTV8`
    - `x-flychain-active-adapter-capability-id=phase1-exact-sentinel-2026-05-03`
  - persisted trace provider/model: `local-mlx` /
    `mlx-community/Llama-3.2-3B-Instruct-4bit`
  - persisted trace request included the trained adapter directory.
  - active trace auto-eval passed with `evaluator_type=deterministic` and
    `evaluator_source=deterministic:exact_match`.

## Final Decision

Passed. Phase 1 real-stack E2E evidence is complete for this gate:

- deterministic wrong outputs failed;
- deterministic evaluator proof persisted;
- failures and corrections were visible;
- corrected failures produced a dataset;
- training used real MLX with fallback disabled;
- activation and promotion were blocked before served validation;
- controlled served-validation failure/retry was visible from API/dashboard;
- served validation passed only through candidate chat routing with adapter
  proof;
- promotion succeeded only after valid served validation;
- active chat used the validated MLX adapter and produced the exact sentinel.
