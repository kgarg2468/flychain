# @flychain/cli

Node CLI for project setup, source instrumentation, and local model bootstrap.

Deep dive:
[../../docs/architecture/dashboard-cli-sdks.md](../../docs/architecture/dashboard-cli-sdks.md)

## Commands

- `flychain init`: writes `flychain.config.json`.
- `flychain instrument`: previews or applies OpenAI/Anthropic constructor
  rewrites so application traffic points at the FlyChain gateway.
- `flychain bootstrap local-models`: starts the Compose Ollama service and
  pulls the local judge and embedding models.

## Usage

```bash
pnpm -F @flychain/cli build
node ./apps/cli/dist/index.js --help
node ./apps/cli/dist/index.js init
node ./apps/cli/dist/index.js instrument
node ./apps/cli/dist/index.js instrument --apply
node ./apps/cli/dist/index.js bootstrap local-models
```
