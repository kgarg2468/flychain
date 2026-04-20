# @flychain/cli

The FlyChain CLI. Ships as a single `flychain` command.

Phase 0 stubs `instrument` and `init`. Phase 2 implements full AST-based instrumentation of `OpenAI(...)` / `Anthropic(...)` client constructors and an optional hand-off to a coding agent for less-structured cases.

## Usage (Phase 0)

```bash
pnpm -F @flychain/cli build
node ./apps/cli/dist/index.js --help
```
