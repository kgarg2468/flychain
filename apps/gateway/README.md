# FlyChain Gateway

FastAPI service that acts as the current FlyChain control plane. It exposes
OpenAI-compatible and Anthropic-compatible proxy endpoints, records traces,
serves feedback and capability APIs, runs eval/clustering/dataset actions, and
queues training or gate jobs for the orchestrator.

Deep dive: [../../docs/architecture/gateway.md](../../docs/architecture/gateway.md)

## Local Dev

```bash
uv sync
uv run uvicorn flychain_gateway.main:app --reload --host 0.0.0.0 --port 8080
```

Or via Docker Compose:

```bash
docker compose up gateway
```

## Main Endpoint Groups

- Health and registry: `GET /healthz`, `GET /version`, `GET /v1/models`
- Proxy: `POST /v1/chat/completions`, `POST /v1/messages`
- Feedback: `POST /v1/feedback`
- Capabilities and compiler: `/v1/capabilities...`
- Eval, traces, failures, scorecards: `/v1/eval`, `/v1/traces`,
  `/v1/capabilities/{id}/failures`, `/v1/capabilities/{id}/scorecard`
- Clusters and datasets: `/v1/capabilities/{id}/cluster-run`,
  `/v1/capabilities/{id}/synthesize-dataset`
- Recipes and runs: `/v1/recipes`, `/v1/training-runs`
- Replay, A/B compare, active adapter pointers:
  `/v1/capabilities/{id}/replay-sets`, `/v1/capabilities/{id}/ab-compare`,
  `/v1/capabilities/{id}/active-adapter`

## State

Trace/eval/feedback data goes to ClickHouse with an in-memory fallback. Local
control-plane state is file-backed under `$FLYCHAIN_DATA_DIR`.
