# Phase 1 Real-Stack E2E Gate Runbook

This runbook is the operational proof required before Phase 1 can be called
ready for Phase 2. It must be run against real local services, with a real MLX
training artifact and real MLX serving.

## Runtime Topology

- Docker services: ClickHouse, Postgres, Redis, Ollama.
- Host services: gateway on `:8080`, orchestrator worker, dashboard on `:3000`,
  MLX server on `:8081`.
- Use host-run Python services because `mlx-lm` training requires Darwin/Apple
  Silicon. The Docker orchestrator container cannot run the real MLX backend.
- Use a fresh `FLYCHAIN_DATA_DIR` for each gate run and a unique capability id
  such as `phase1-exact-sentinel-2026-05-03`.

## Preflight Checks

Run and capture:

```bash
uv run pytest
pnpm -r --if-present test
pnpm -r --if-present typecheck
uv run ruff check apps packages
uv run mypy apps packages
```

## Start Services

Start infra only:

```bash
docker compose up -d clickhouse postgres redis ollama
docker exec flychain-ollama ollama pull llama3.2:3b
docker exec flychain-ollama ollama pull nomic-embed-text
```

Set shared host env:

```bash
export FLYCHAIN_DATA_DIR="$PWD/.flychain-phase1-e2e-$(date +%Y%m%dT%H%M%S)"
export FLYCHAIN_CLICKHOUSE_URL="http://flychain:flychain@localhost:8123/flychain"
export FLYCHAIN_POSTGRES_URL="postgresql://flychain:flychain@localhost:5432/flychain"
export FLYCHAIN_REDIS_URL="redis://localhost:6379/0"
export FLYCHAIN_OLLAMA_URL="http://localhost:11434"
export FLYCHAIN_GATEWAY_URL="http://localhost:8080"
export FLYCHAIN_MLX_SERVER_URL="http://127.0.0.1:8081"
export FLYCHAIN_MODELS_YAML="$PWD/apps/gateway/src/flychain_gateway/_assets/models.yaml"
export FLYCHAIN_TEMPLATES_DIR="$PWD/packages/capability-compiler/src/flychain_capability_compiler/_assets/templates"
export FLYCHAIN_RECIPES_DIR="$PWD/packages/capability-compiler/src/flychain_capability_compiler/_assets/recipes"
```

Start the host services in separate terminals:

```bash
uv run uvicorn flychain_gateway.main:app --host 0.0.0.0 --port 8080
uv run arq flychain_orchestrator.worker.WorkerSettings
FLYCHAIN_GATEWAY_URL=http://localhost:8080 NEXT_PUBLIC_FLYCHAIN_GATEWAY_URL=http://localhost:8080 pnpm -F @flychain/dashboard dev
```

Do not start the MLX server until after the first served-validation job has
failed or timed out. This controlled failure proves job visibility and retry.

## Gate Procedure

1. Verify `/healthz`, `/v1/settings`, `/v1/jobs`, and the dashboard.
2. Create a fresh exact-match sentinel capability requiring exactly
   `PHASE1_SENTINEL_OK`, with no trim or whitespace normalization.
3. Enable `auto_eval_new_traces` and `auto_cluster_failures`.
4. Send at least three tagged chat requests through `/v1/chat/completions` that
   produce `PHASE1_SENTINEL_BAD`.
5. Verify deterministic eval failures with `evaluator_type=deterministic` and
   `evaluator_source=deterministic:exact_match`.
6. Add feedback corrections of exactly `PHASE1_SENTINEL_OK`.
7. Verify failures and clustering/job visibility from API or dashboard.
8. Synthesize an SFT dataset from corrected failures.
9. Queue real MLX training with `allow_backend_fallback=false` and recipe
   `sft-mlx-lora-local-3b`.
10. Verify the run artifact is real MLX: `backend=mlx-lm`, not dry-run,
    expected base model, nonempty adapter directory.
11. Attempt activation and promotion before served validation; both must be
    blocked.
12. Create a replay set expecting `PHASE1_SENTINEL_OK`.
13. Queue served validation while MLX is still down and verify failed, timed
    out, or retrying job state is visible.
14. Start MLX with the trained artifact:

```bash
uv run python -m mlx_lm server \
  --model mlx-community/Llama-3.2-3B-Instruct-4bit \
  --adapter-path "$ADAPTER_DIR" \
  --host 127.0.0.1 \
  --port 8081 \
  --temp 0 \
  --max-tokens 16
```

15. Retry the failed served-validation job or queue a new one.
16. Verify served validation records output, eval result, provider, model,
    trace id, adapter run id, adapter capability id, routing mode, and no
    failures.
17. Apply the promotion gate with baseline `0.0` and candidate `1.0`.
18. Verify the active adapter pointer matches the validated run.
19. Send a normal chat request with only the capability header and verify
    active adapter proof headers and trace metadata.
20. Save the evidence in `docs/e2e/phase1-real-stack-YYYY-MM-DD.md`.

## Acceptance

Phase 1 is ready for Phase 2 only when all of these are true:

- Wrong sentinel outputs fail deterministic exact match.
- The deterministic evaluator proof is persisted and inspectable.
- Failure records include trace id, prompt, wrong output, reason, and correction
  status.
- Dataset synthesis uses corrected failures and produces nonempty SFT rows.
- Training is real MLX; dry-run or fallback fails the gate.
- Activation and promotion are blocked before served validation.
- Served validation uses `/v1/chat/completions` candidate routing and records
  adapter proof metadata.
- Promotion succeeds only after valid served validation.
- Active adapter chat uses the validated run.
- A controlled failed, timed-out, or retrying job is visible and recoverable
  from API or dashboard without database/log inspection.
