# @flychain/cli

The FlyChain CLI. Ships as a single `flychain` command.

Ships three commands today:

- `init` writes `flychain.config.json`
- `instrument` patches supported Python/TypeScript OpenAI clients
- `bootstrap local-models` pulls the local Ollama models FlyChain expects for judge + embeddings

## Usage (Phase 0)

```bash
pnpm -F @flychain/cli build
node ./apps/cli/dist/index.js --help
node ./apps/cli/dist/index.js bootstrap local-models
```
