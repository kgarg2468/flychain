# Phase 2 Real-Stack E2E Evidence - 2026-05-03

Status: passed

## Environment

- Repo: `/Users/krishgarg/Documents/Projects/flychain`
- Runtime data dir: `/Users/krishgarg/Documents/Projects/flychain/.flychain-phase2-e2e-phase2-exact-sentinel-20260503T180948`
- Capability id: `phase2-exact-sentinel-20260503T180948`
- Project id: `phase2-exact-sentinel-20260503T180948`
- Gateway: `http://localhost:8080`
- Dashboard: `http://localhost:3000`
- MLX server: `http://127.0.0.1:8081`
- Adapter run: `run_01KQR8NSTBAFBW107F1DK0R4K2`
- Dataset: `ds_01KQR8NST5KGEMCM81K5KB9CXY`
- Cluster: `phase2-exact-sentinel-20260503T180948-c0`
- Failed served-validation job kept for visibility: `job_01KQR8RQQNKWY4MHZBA6FPA5S0`
- Passed served-validation job: `job_01KQR8TDQYRX9HXK9R338MQNZF`

## Automated Checks

Fresh preflight before the Browser Use gate:

| Command | Result |
| --- | --- |
| `uv run pytest` | Passed: 151 passed, 5 warnings |
| `pnpm -r --if-present test` | Passed: dashboard 18, CLI 24, SDK TS 4 |
| `pnpm -r --if-present typecheck` | Passed |
| `uv run ruff check apps packages` | Passed |
| `uv run mypy apps packages` | Passed |
| `pnpm --filter @flychain/dashboard lint` | Passed |
| `git diff --check` | Passed |

Focused checks after the runtime health and ClickHouse migration patches:

| Command | Result |
| --- | --- |
| `uv run pytest apps/gateway/tests/test_observability.py::test_settings_endpoint_is_env_first_and_persists_local_knobs` | Passed |
| `pnpm --filter @flychain/dashboard test -- src/app/settings/client.test.tsx` | Passed |
| `pnpm --filter @flychain/dashboard typecheck` | Passed |

Final verification after all patches and the browser gate:

| Command | Result |
| --- | --- |
| `uv run pytest` | Passed: 151 passed, 5 warnings |
| `pnpm -r --if-present test` | Passed: dashboard 18, CLI 24, SDK TS 4 |
| `pnpm -r --if-present typecheck` | Passed |
| `uv run ruff check apps packages` | Passed |
| `uv run mypy apps packages` | Passed: no issues in 36 source files |
| `pnpm --filter @flychain/dashboard lint` | Passed |
| `git diff --check` | Passed |

## Browser-Only Gate

The final dashboard gate used Browser Use against the in-app browser. During the gate, I did not use curl, local files, database tools, or worker logs to inspect product evidence. The terminal was only used to start or recover the local test stack and to run verification commands.

The first browser pass found real Phase 2 visibility gaps: the capability page did not show enough cluster readiness, dataset provenance, training artifact/backend/scores, or before/after adapter metadata. Those UI fields were added, and the browser gate was rerun.

The gateway restart then exposed a live ClickHouse schema drift issue: `eval_scores` needed `evaluator_type` and `evaluator_source`. A startup migration was added so evaluator proof persists in ClickHouse instead of relying on the process buffer. Existing Phase 2 traces were re-evaluated after the migration. Historical dashboard-chat traces that were accidentally evaluated against this capability were marked `not_useful`, preserving the audit trail while leaving one actionable unresolved Phase 2 failure.

## Capability Flywheel

Final capability screenshot: [10-capability-final-after-chat.png](phase2-real-stack-2026-05-03-assets/10-capability-final-after-chat.png)

The dashboard shows the complete loop:

- summary counts: `TRACES=24`, `EVALUATED=23`, `FAILURES=17`, `UNRESOLVED=1`, `CLUSTERS=1`, `DATASETS=1`, `RUNS=1`
- latest served validation: `passed`
- active adapter run: `run_01KQR8NSTBAFBW107F1DK0R4K2`
- last adapted chat: `trace_01KQR9GZYX9YAJG7Y7KRM3RM7T`
- adapter provider/model: `local-mlx` / `mlx-community/Llama-3.2-3B-Instruct-4bit`
- lifecycle steps: capture, evaluate, detect failures, collect corrections, cluster, synthesize dataset, train, validate served adapter, promote, serve active adapter

## Failure Inbox Actions

Action screenshot: [03-failure-inbox-actions.png](phase2-real-stack-2026-05-03-assets/03-failure-inbox-actions.png)

Final capability screenshot also shows the persisted result:

- unresolved failure: `trace_01KQR9ESMAFN5712QSPEE7CN2R`, bad output `PHASE2_SENTINEL_BAD`, no correction, `dataset blocked`
- saved correction: `trace_01KQR8TRG30697Z8QX9YM0V5AK`, correction `PHASE2_SENTINEL_OK`, `dataset eligible`
- excluded failure: `trace_01KQR8TN535QJRD5V9XVVEFWZ4`, review status `not_useful`, `dataset blocked`
- evaluator proof: `exact_sentinel / expected exact match 'PHASE2_SENTINEL_OK' / deterministic:exact_match`

## Cluster, Dataset, Training, And Before/After

Same final capability screenshot: [10-capability-final-after-chat.png](phase2-real-stack-2026-05-03-assets/10-capability-final-after-chat.png)

The dashboard shows:

- cluster `phase2-exact-sentinel-20260503T180948-c0`
- representative trace ids and prompts
- readiness: `ready`
- correction coverage: `3/3 corrected`
- latest dataset: `ds_01KQR8NST5KGEMCM81K5KB9CXY`
- dataset source cluster, method `sft`, row count `3`, correction source `human 3 / generated 0`, path, and downstream run
- training run recipe `sft-mlx-lora-local-3b`
- backend `mlx-lm`
- offline score `1.00`
- served validation score `1.00`
- artifact path
- distinct states: `trained`, `validated`, `promoted`, `active`
- before/after for the same prompt:
  - baseline output: `PHASE2_SENTINEL_BAD`
  - adapted output: `PHASE2_SENTINEL_OK`
  - routing mode: `candidate`
  - evaluator score: `exact_sentinel / 1.00 / passed / exact match / deterministic:exact_match`

## Jobs And Runtime Health

Jobs screenshot: [11-final-jobs.png](phase2-real-stack-2026-05-03-assets/11-final-jobs.png)

- Jobs tab shows recent background work.
- Failed served-validation job remains visible: `job_01KQR8RQQNKWY4MHZBA6FPA5S0`, status `failed`.
- Successful served-validation job remains visible: `job_01KQR8TDQYRX9HXK9R338MQNZF`, status `succeeded`.

Settings screenshot: [11-final-settings.png](phase2-real-stack-2026-05-03-assets/11-final-settings.png)

- Settings deep link lands on the workspace Settings tab.
- Runtime status shows data dir, Ollama URL, MLX server URL, ClickHouse URL, and Redis URL.
- Component health shows `ok` for Gateway, Background jobs, ClickHouse, Redis, Postgres, Ollama, and MLX server.

## Chat And Deep Links

Chat screenshot: [09-chat-final-active-adapter.png](phase2-real-stack-2026-05-03-assets/09-chat-final-active-adapter.png)

Dashboard Chat with the Phase 2 capability selected returned:

- user prompt: `Phase 2 final browser gate...`
- adapted output: `PHASE2_SENTINEL_OK`
- trace id: `trace_01KQR9GZYX9YAJG7Y7KRM3RM7T`
- active adapter run: `run_01KQR8NSTBAFBW107F1DK0R4K2`
- provider/model: `local-mlx` / `mlx-community/Llama-3.2-3B-Instruct-4bit`
- adapter capability: `phase2-exact-sentinel-20260503T180948`
- eval state shown in the chat UI: `Eval pending` while background auto-eval completed

Deep-link screenshots:

- Chat: [11-final-chat-deeplink.png](phase2-real-stack-2026-05-03-assets/11-final-chat-deeplink.png), `/chat` -> `/?tab=chat`
- Traces: [11-final-traces.png](phase2-real-stack-2026-05-03-assets/11-final-traces.png), `/traces?capability_id=...` -> `/?tab=traces&capability_id=...`
- Settings: [11-final-settings.png](phase2-real-stack-2026-05-03-assets/11-final-settings.png), `/settings` -> `/?tab=settings`
- Capability detail: [11-final-capability-deeplink.png](phase2-real-stack-2026-05-03-assets/11-final-capability-deeplink.png), `/capabilities/{id}#runs` -> selected capability in the workspace

## Final Decision

Passed. Phase 2 real-stack E2E evidence is complete for this gate:

- one capability's lifecycle is explainable from the dashboard;
- failures show prompt, bad output, evaluator reason/source, correction state, review state, cluster membership, and dataset eligibility;
- clusters show readiness and correction coverage;
- datasets show source and downstream run provenance;
- training runs distinguish trained, validated, promoted, and active states;
- before/after shows baseline output, adapted output, evaluator scores, adapter metadata, and final verdict;
- chat exposes active adapter proof metadata;
- failed background work is visible from the Jobs tab;
- runtime dependencies are visible from Settings;
- old deep links land on the connected workspace state.
