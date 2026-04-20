# FlyChain gateway

FastAPI service that exposes OpenAI- and Anthropic-compatible endpoints, proxies requests to the configured upstream provider, records traces to ClickHouse, emits OTel / OpenInference spans, and serves the `/v1/feedback` endpoint.

Phase 0 ships a health-check scaffold. Phase 1 adds the proxy and trace writes.

## Local dev

```bash
uv sync
uv run uvicorn flychain_gateway.main:app --reload --host 0.0.0.0 --port 8080
```

Or via Docker:

```bash
docker compose up gateway
```

## Endpoints (Phase 0)

- `GET /healthz`
- `GET /version`

## Endpoints (Phase 1, planned)

- `POST /v1/chat/completions` (OpenAI-compatible)
- `POST /v1/messages` (Anthropic-compatible)
- `POST /v1/feedback`
