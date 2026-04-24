# FlyChain Orchestrator

arq-based worker process for FlyChain background jobs. It consumes Redis jobs
created by the gateway and updates shared state under `$FLYCHAIN_DATA_DIR`.

Deep dives:

- [System overview](../../docs/architecture/system-overview.md)
- [Capability flywheel](../../docs/architecture/capability-flywheel.md)

## Jobs

- `noop`: health/check task.
- `evaluate_trace`: calls gateway `POST /v1/eval`.
- `run_training_recipe`: loads a training run, selects a backend, writes
  artifacts, and marks the run trained or failed.
- `apply_promotion_gate`: applies the recipe gate and writes the active adapter
  pointer when promoted.

## Local Dev

```bash
uv sync
uv run arq flychain_orchestrator.worker.WorkerSettings
```

Or via Docker Compose:

```bash
docker compose up orchestrator
```
