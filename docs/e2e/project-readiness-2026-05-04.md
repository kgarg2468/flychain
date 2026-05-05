# Project Readiness Audit - 2026-05-04

Status: ready for Phase 5 planning / no known blocking issues

This report means the project is a verified local-first control plane with known limits. It is not a zero-defect guarantee.

## Scope

This audit followed the Phase 3/4 E2E documentation cleanup plan:

- create permanent Phase 3 and Phase 4 real-stack evidence;
- rerun fresh dashboard E2E flows with Browser Use;
- run the full automated gate;
- check runtime service health;
- smoke the dashboard after `next build`;
- review Phase 3/4 source paths for obvious blockers;
- commit only Markdown evidence and screenshot assets.

## Fresh E2E Evidence

Phase 3:

- Report: [phase3-real-stack-2026-05-04.md](phase3-real-stack-2026-05-04.md)
- Capability: `phase3-guided-sentinel-20260504T205000`
- Result: passed
- Key proof: final dashboard Chat returned `PHASE3_SENTINEL_OK` with active adapter `run_01KQV46FMC9CPXBSE91VEXCYYH`.

Phase 4 human-correction policy:

- Report: [phase4-real-stack-2026-05-04.md](phase4-real-stack-2026-05-04.md)
- Capability: `phase4-autopilot-sentinel-20260504T205000`
- Result: passed
- Key proof: human-only policy blocked below threshold, created dataset after three human corrections, trained real MLX, passed served validation, waited for operator promotion approval, promoted after approval, and rollback removed active adapter proof from the next chat.

Phase 4 generated-correction policy:

- Report: [phase4-real-stack-2026-05-04.md](phase4-real-stack-2026-05-04.md)
- Capability: `phase4-generated-sentinel-20260504T205000`
- Result: passed with one non-blocking UI wording warning
- Key proof: generated-correction policy showed `human 0 / generated 3`, trained real MLX, passed served validation, auto-promoted with `auto_promote=true` and `require_promotion_approval=false`, and final dashboard Chat returned `PHASE4_GENERATED_OK` with active adapter `run_01KQV4XR0N56CN88N6Z0DT8Y4S`.

## Automated Gate

Final command results:

| Command | Result |
| --- | --- |
| `git diff --check` | Passed |
| skipped/debug search | Passed with expected non-blocking hits only: CLI `console.log` output and Tailwind `w-fit` class names |
| `uv run pytest` | Passed: 161 passed, 5 sklearn `FutureWarning`s |
| `uv run ruff check apps packages` | Passed |
| `uv run mypy apps packages` | Passed: no issues in 37 source files |
| `pnpm -r --if-present test` | Passed: dashboard 23, CLI 24, SDK TS 4 |
| `pnpm -r --if-present typecheck` | Passed |
| `pnpm -r --if-present lint` | Passed |
| `pnpm -r --if-present build` | Passed |
| `pnpm format:check` | Passed |

Note: the first `pnpm format:check` run failed because Prettier scanned the untracked `.flychain-e2e-doc-*` runtime data directories created by the fresh E2E runs. Those untracked generated files were formatted in place, then the exact required `pnpm format:check` command passed. No tracked product code was changed for that fix.

## Runtime Health

`docker compose ps` showed all required containers running and healthy:

- `flychain-clickhouse`: healthy
- `flychain-ollama`: healthy
- `flychain-postgres`: healthy
- `flychain-redis`: healthy

Gateway health:

- `GET /healthz`: `{"status":"ok"}`

`GET /v1/settings` runtime health:

- Gateway: `ok`
- Background jobs: `ok`
- ClickHouse: `ok`
- Redis: `ok`
- Postgres: `ok`
- Ollama: `ok` with `http 200`
- MLX server: `ok` with `http 200`

Active runtime data dir during final health check:

```text
/Users/krishgarg/Documents/Projects/flychain/.flychain-e2e-doc-phase4-generated-20260504T205000
```

## Browser Use Smoke

The dashboard dev server was restarted after `pnpm -r --if-present build`.

Browser Use smoke passed with no visible Next/runtime error on:

- capability page for `phase4-generated-sentinel-20260504T205000`;
- Autopilot policy panel;
- Autopilot audit table;
- Guided Actions panel;
- Chat deep link: `/chat`;
- Traces deep link: `/traces?capability_id=phase4-generated-sentinel-20260504T205000`;
- Jobs tab: `/?tab=jobs`;
- Settings deep link: `/settings`;
- Capability deep link: `/capabilities/phase4-generated-sentinel-20260504T205000#runs`.

Additional smoke screenshot:

- [16-browser-smoke-settings.png](phase4-real-stack-2026-05-04-assets/16-browser-smoke-settings.png)

## Targeted Source Review

Reviewed paths:

- gateway guided action execution and guardrails in `apps/gateway/src/flychain_gateway/main.py`;
- autopilot policy, audit, approval, and rollback endpoints in `apps/gateway/src/flychain_gateway/main.py`;
- correction provenance and generated-correction eligibility in `apps/gateway/src/flychain_gateway/main.py` and `apps/gateway/src/flychain_gateway/trace_store.py`;
- file-backed autopilot policy/audit/history store in `apps/gateway/src/flychain_gateway/autopilot_store.py`;
- dashboard typed helpers in `apps/dashboard/src/lib/gateway.ts`;
- dashboard policy/guided-action panels in `apps/dashboard/src/app/workspace-client.tsx`;
- checked-in Phase 3 and Phase 4 E2E docs for accurate capability IDs, run IDs, job IDs, and caveats.

Review result:

- no blocker found in guided action approval requirements;
- no blocker found in `allow_backend_fallback=false` for guided/autopilot training;
- no blocker found in served-validation proof requirements before promotion;
- no blocker found in policy-disabled behavior or audit recording;
- no blocker found in rollback audit behavior;
- one non-blocking UI wording warning remains for generated corrections, listed below.

## Blockers

None known after this audit.

## Non-Blocking Warnings

- Generated-correction Failure Inbox wording: generated-corrected rows still display `dataset blocked` in the Failure Inbox even when policy allows generated dataset rows. The dataset summary and audit correctly show `human 0 / generated 3`, and the engine correctly trained, validated, and auto-promoted. This is a UI wording/eligibility-display cleanup, not an unsafe automation behavior.
- `uv run pytest` emits 5 sklearn `FutureWarning`s from HDBSCAN defaults. Tests pass.
- `pnpm -r --if-present test` emits Vite's CJS Node API deprecation warning. Tests pass.
- The E2E runtime data directories remain untracked and are intentionally not staged:
  - `.flychain-e2e-doc-phase3-20260504T204312/`
  - `.flychain-e2e-doc-phase3-20260504T205000/`
  - `.flychain-e2e-doc-phase4-human-20260504T205000/`
  - `.flychain-e2e-doc-phase4-generated-20260504T205000/`

## Final Decision

Go. The project is ready to move past Phase 4 documentation/readiness with no known blockers.

The accurate claim is:

```text
FlyChain is a working local-first control plane with verified Phase 1 through Phase 4 gates, fresh real-stack Phase 3/4 dashboard evidence, passing automated checks, healthy required runtime services, and explicitly documented non-blocking warnings.
```

The inaccurate claim would be:

```text
FlyChain has zero bugs.
```
