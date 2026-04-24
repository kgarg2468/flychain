# Dashboard, CLI, And SDKs

This doc covers user-facing TypeScript surfaces: the Next.js dashboard, the
Node CLI, and the thin SDK packages.

## Dashboard

The dashboard lives in `apps/dashboard`. It is a Next.js 14 App Router app with
server components for data loading and client components for operator actions.

### Gateway Client

`apps/dashboard/src/lib/gateway.ts` is the dashboard's typed gateway client.
It reads `FLYCHAIN_GATEWAY_URL` on the server and defaults to
`http://localhost:8080`.

The client covers:

- Capability templates and CRUD.
- Capability compiler questions and compile.
- Scorecards.
- Trace listing.
- Failure listing.
- Cluster runs and cluster listing.
- Dataset synthesis and dataset listing.
- Replay sets.
- Recipes.
- Training runs.
- A/B comparison.
- Gate application.
- Active adapter pointer operations.
- Settings read/update.

### Same-Origin API Routes

Browser-initiated capability creation and compiler calls go through Next API
routes in `apps/dashboard/src/app/api/capabilities/...`. These routes proxy the
request body to the gateway and preserve the response status/content type.

This avoids browser CORS concerns and keeps the browser talking to the
dashboard origin.

### Pages

| Route                | File                                                    | Purpose                                                                                                |
| -------------------- | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `/`                  | `src/app/page.tsx`                                      | Workspace overview: capabilities, scorecards, active adapter delta, entry actions                      |
| `/capabilities/new`  | `src/app/capabilities/new/page.tsx` and `client.tsx`    | Template mode and describe/compiler mode                                                               |
| `/capabilities/[id]` | `src/app/capabilities/[id]/page.tsx` and `controls.tsx` | Capability detail, scorecard, clusters, datasets, active adapter, training runs, and operator controls |
| `/traces`            | `src/app/traces/page.tsx`                               | Trace explorer with project, capability, provider, and status filters                                  |
| `/settings`          | `src/app/settings/page.tsx` and `client.tsx`            | Non-secret runtime settings and environment status                                                     |

### Capability Controls

`controls.tsx` is the main operator action surface. It supports:

- Selecting failures and running clustering.
- Synthesizing SFT/DPO datasets from stored clusters.
- Queueing training runs.
- Creating or updating replay sets.
- Running A/B comparison from replay sets.
- Applying the gate from latest comparison or explicit score maps.
- Activating or clearing adapter pointers.

The component refreshes the route every three seconds while any run is in an
active status: `queued`, `running`, `gate-queued`, or `gate-running`.

### Dashboard Test Surface

The dashboard uses Vitest and Testing Library. The current test files cover the
home page, settings client, and capability controls.

Run dashboard tests:

```bash
pnpm -F @flychain/dashboard test
```

## CLI

The CLI lives in `apps/cli` and exposes the `flychain` command. Source entrypoint
is `apps/cli/src/index.ts`.

### Commands

`flychain init`

- Writes `flychain.config.json` in the current directory.
- Defaults project ID from current directory name.
- Accepts `--project-id`, `--gateway-url`, repeated `--tag key=value`, and
  `--force`.

`flychain instrument`

- Requires `flychain.config.json`.
- Discovers Python and TypeScript/JavaScript source files while ignoring common
  build and dependency directories.
- Detects OpenAI and Anthropic constructors with regex-based matching.
- Preview mode is default.
- `--apply` rewrites constructors with FlyChain base URL and default headers.

`flychain bootstrap local-models`

- Starts the Compose Ollama service.
- Pulls `llama3.2:3b` and `nomic-embed-text` inside the `flychain-ollama`
  container.

### Project Config

`flychain.config.json` shape:

```json
{
  "projectId": "my-project",
  "gatewayUrl": "http://localhost:8080",
  "tags": {},
  "providers": ["openai", "anthropic", "local-ollama"],
  "capabilities": [],
  "version": 1
}
```

The config is currently consumed by CLI instrumentation. Gateway runtime config
is env-based.

### Instrumentation Caveat

The CLI rewriter injects `x-flychain-project` and per-tag headers named
`x-flychain-tags-<key>`. The gateway currently parses tags from a single
`x-flychain-tags` header containing comma-separated `key=value` pairs. Treat
automatic tag propagation from CLI rewrites as incomplete until those formats
are aligned.

## TypeScript SDK

`packages/sdk-ts` currently exports:

- `VERSION`
- `FlyChainConfig`
- `gatewayBaseUrl(defaultUrl?)`
- `resolveConfig(overrides?)`

It resolves `gatewayUrl`, optional `apiKey`, and optional `projectId` from
overrides or environment. It currently does not wrap gateway endpoints.

## Python SDK

`packages/sdk-py` currently exports:

- `__version__`
- `gateway_base_url(default="http://localhost:8080")`

It reads `FLYCHAIN_GATEWAY_URL` and otherwise returns the supplied default. It
currently does not wrap gateway endpoints.

## How To Extend These Surfaces

For a new gateway API:

1. Add or confirm the FastAPI route.
2. Add a typed method in `apps/dashboard/src/lib/gateway.ts`.
3. If it is browser-initiated and needs same-origin behavior, add a Next API
   proxy route.
4. Add or update the relevant page/client component.
5. Add targeted tests.

For CLI behavior:

1. Add command module under `apps/cli/src/commands`.
2. Wire it in `apps/cli/src/index.ts`.
3. Add tests using Node's built-in test runner.
4. Keep filesystem mutations behind explicit flags where preview mode is useful.
