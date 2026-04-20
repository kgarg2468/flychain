# FlyChain orchestrator

`arq`-based worker process that drives the capability flywheel:

1. Consume new traces and evaluate them per capability (Phase 4).
2. Embed failures and cluster them with HDBSCAN (Phase 5).
3. Synthesize training datasets from clusters (Phase 5).
4. Execute training recipes against the appropriate backend (Phase 6).
5. Apply the auto-promote gate (Phase 6).

Phase 0 ships a scaffold with a single `noop` task.

## Local dev

```bash
uv sync
uv run arq flychain_orchestrator.worker.WorkerSettings
```

Or via Docker:

```bash
docker compose up orchestrator
```
