# FlyChain Architecture

This directory is the maintainer guide for the implemented FlyChain system.
The top-level [README](../../README.md) is the orientation map; these docs go
deeper into subsystem responsibilities, request flow, persistence, and extension
points.

## Reading Order

1. [System overview](./system-overview.md) - service topology, ownership, and
   end-to-end lifecycle.
2. [Gateway](./gateway.md) - FastAPI control plane, model proxying, endpoint
   groups, local stores, queue handoff, and observability.
3. [Capability flywheel](./capability-flywheel.md) - capability specs,
   compiler, eval, clustering, datasets, recipes, training backends, and gate.
4. [Dashboard, CLI, and SDKs](./dashboard-cli-sdks.md) - operator UI,
   same-origin proxy routes, CLI behavior, and current SDK surface.
5. [Persistence and config](./persistence-and-config.md) - ClickHouse, Redis,
   Ollama, file-backed state, Postgres status, and env precedence.
6. [Roadmap](./roadmap.md) - intended future architecture. This is explicitly
   not the current implementation guide.

## Current Source Of Truth

- Gateway runtime: `apps/gateway/src/flychain_gateway/main.py`
- Orchestrator jobs: `apps/orchestrator/src/flychain_orchestrator/worker.py`
- Dashboard gateway client: `apps/dashboard/src/lib/gateway.ts`
- Capability primitives:
  `packages/capability-compiler/src/flychain_capability_compiler/`
- ClickHouse schema: `infra/clickhouse/init/001_schema.sql`
- Local stack: `docker-compose.yml`

## What Is Implemented Today

- Model proxying for OpenAI-compatible chat completions, local Ollama via the
  OpenAI-compatible path, and Anthropic messages.
- Trace, eval score, and feedback writes to ClickHouse with an in-memory
  fallback.
- Capability creation from templates or compiler output.
- LLM-as-judge eval and scorecard aggregation.
- Failure derivation from traces, eval scores, and feedback.
- HDBSCAN failure clustering and SFT/DPO dataset synthesis.
- Training run queueing, backend execution, A/B comparison, promotion gate, and
  active adapter pointer persistence.
- Dashboard surfaces for the above flows.

## Not Implemented Today

- Postgres-backed metadata.
- Streaming proxy responses.
- Automatic scheduled clustering.
- Dynamic runtime serving of active adapters.
- Full Python or TypeScript gateway client SDKs.
- Hosted/cloud training backends.
