# FlyChain

**An open-source (Apache-2.0) flywheel for making models better at specific capabilities.**

FlyChain collects real traces, automatically evaluates them, clusters repeated failure modes, turns those failures into targeted training data, trains new model variants with SFT (and optionally DPO), and only promotes versions that measurably improve on the chosen capability.

> Promise: **not** "every run improves," but "every run is measured, and only better versions ship."

See [plan.md](./plan.md) for the full product requirements document and roadmap.

## V1 at a glance

- **Local-only** on a 16 GB MacBook. No cloud dependencies in the critical path.
- Target **1B-3B** base models (Llama-3.2-1B/3B, Qwen2.5-1.5B/3B, Phi-3.5-mini).
- Local inference via **Ollama**. Local eval judge and embeddings. Local LoRA training via **MLX-LM** (Apple Silicon) or **unsloth** (CUDA Linux).
- Ships 5 capability templates: **groundedness, instruction following, code correctness, uncertainty calibration, multi-step reasoning**.

## Repo layout

```
apps/
  gateway/        FastAPI OpenAI/Anthropic-compatible proxy + /v1/feedback
  dashboard/      Next.js 14 App Router UI (capability workspace, trace explorer, triage)
  cli/            `flychain instrument` Node CLI
  orchestrator/   arq workers: eval, cluster, dataset-synth, train, gate
packages/
  sdk-py/              Python SDK
  sdk-ts/              TypeScript SDK
  capability-compiler/ NL -> CapabilitySpec pipeline + JSON schema
capabilities/
  templates/      5 shipped capability templates (YAML)
recipes/          Training recipes (YAML) - forkable
evals/
  judge-prompts/  Default LLM-as-judge templates per capability dimension
docker-compose.yml
```

## Quick start (local dev)

Prerequisites: Docker Desktop, Node 20+, pnpm, Python 3.11+.

```bash
# install JS workspace deps
pnpm install

# install Python workspace deps (uv-managed)
uv sync

# bring up the local stack
docker compose up -d

# open the dashboard
open http://localhost:3000
```

## Status

Phase 0 (repo scaffold + docker-compose + CI) in progress. Phases are tracked in [plan.md](./plan.md).

## License

Apache-2.0. See [LICENSE](./LICENSE).

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md).
