# Phase 3 Real-Stack E2E Evidence - 2026-05-04

Status: passed

## Environment

- Repo: `/Users/krishgarg/Documents/Projects/flychain`
- Runtime data dir: `/Users/krishgarg/Documents/Projects/flychain/.flychain-e2e-doc-phase3-20260504T205000`
- Capability id: `phase3-guided-sentinel-20260504T205000`
- Gateway: `http://localhost:8080`
- Dashboard: `http://localhost:3000`
- MLX server: `http://127.0.0.1:8081`
- Expected output: `PHASE3_SENTINEL_OK`
- Baseline bad output: `PHASE3_SENTINEL_BAD`
- Cluster: `phase3-guided-sentinel-20260504T205000-c0`
- Dataset: `ds_01KQV45ZNZV8FCD9C66FZ13EQS`
- Training run: `run_01KQV46FMC9CPXBSE91VEXCYYH`
- Training job: `job_01KQV46FMDND26EX4JZBCBR1AN`
- Managed replay set: `replay_01KQV4AAG8TFF4KGVZSGVE964E`
- Served validation job: `job_01KQV4AAG8TFF4KGVZSGVE964F`
- Final active-adapter chat trace: `trace_01KQV4G75XEP2WQ7M5C93FFZTV`

## Browser Gate Scope

Browser Use drove the dashboard in the in-app browser. Terminal/API usage was limited to stack setup, health checks, and background status polling; dataset creation, training, served validation, and promotion were executed from dashboard Guided Actions.

Autopilot stayed disabled for this Phase 3 run.

## Evidence Walkthrough

### 1. Fresh guided sentinel capability and dashboard chat failures

Screenshot: [01-chat-failures.png](phase3-real-stack-2026-05-04-assets/01-chat-failures.png)

The Chat tab selected `Phase 3 Guided Sentinel` and produced three failing traces:

- `trace_01KQV43CN790BST538A2C16FSN`
- `trace_01KQV43ATS4S9X1EYE1PC98K6P`
- `trace_01KQV439V0ZB7R16T7Z170J7K4`

### 2. Failure inbox before and after UI corrections

Before corrections: [02-failure-inbox-before-corrections.png](phase3-real-stack-2026-05-04-assets/02-failure-inbox-before-corrections.png)

Corrections saved: [03-corrections-saved.png](phase3-real-stack-2026-05-04-assets/03-corrections-saved.png)

The Failure Inbox showed bad outputs, evaluator reason/source, cluster membership, review state, and dataset eligibility. Corrections were added from the dashboard only:

- correction value: `PHASE3_SENTINEL_OK`
- correction source: human
- corrected eligible failures: `3`

### 3. Guided dataset creation

Screenshot: [04-guided-dataset-created.png](phase3-real-stack-2026-05-04-assets/04-guided-dataset-created.png)

The guided `create_dataset` action created `ds_01KQV45ZNZV8FCD9C66FZ13EQS` from the ready cluster.

Evidence shown in the dashboard:

- included rows: `3`
- skipped rows: `0`
- method: `sft`
- `generate_missing=false`
- correction source summary: human rows only

### 4. Training approval and queued training

Approval panel: [05-training-approval.png](phase3-real-stack-2026-05-04-assets/05-training-approval.png)

Queued state: [06-training-queued.png](phase3-real-stack-2026-05-04-assets/06-training-queued.png)

The guided `start_training` action required inline approval. The approval panel showed:

- recipe: `sft-mlx-lora-local-3b`
- backend: `mlx-lm`
- dataset rows: `3`
- MLX health: `ok / http://127.0.0.1:8081 / http 200`
- fallback policy: `fallback disabled`

The resulting run was `run_01KQV46FMC9CPXBSE91VEXCYYH`. The final run artifact was real MLX output with `dry_run=false` and `allow_backend_fallback=false`.

### 5. Served validation through the real chat-serving path

Training complete and validation ready: [07-training-complete-validation-ready.png](phase3-real-stack-2026-05-04-assets/07-training-complete-validation-ready.png)

Validation started: [08-served-validation-started.png](phase3-real-stack-2026-05-04-assets/08-served-validation-started.png)

Validation passed: [08-served-validation-passed.png](phase3-real-stack-2026-05-04-assets/08-served-validation-passed.png)

The guided `run_served_validation` action created/reused managed replay set `replay_01KQV4AAG8TFF4KGVZSGVE964E` and queued job `job_01KQV4AAG8TFF4KGVZSGVE964F`.

Served validation evidence:

- status: `passed`
- aggregate score: `1.0`
- sample count: `3`
- provider/model: `local-mlx` / `mlx-community/Llama-3.2-3B-Instruct-4bit`
- candidate adapter run: `run_01KQV46FMC9CPXBSE91VEXCYYH`
- routing mode: `candidate`
- validation traces:
  - `trace_01KQV4AAS1H92QNEG6ZHCRFAAY`
  - `trace_01KQV4ACN1SAHF124RTD3M28NH`
  - `trace_01KQV4ACZ2Z8JGHSRRE9QYZXG8`

### 6. Promotion approval and active adapter

Promotion approval: [09-promotion-approval.png](phase3-real-stack-2026-05-04-assets/09-promotion-approval.png)

Promoted active state: [10-promoted-active.png](phase3-real-stack-2026-05-04-assets/10-promoted-active.png)

The guided `promote_adapter` action required inline approval after served validation passed. After approval, the dashboard showed:

- active run: `run_01KQV46FMC9CPXBSE91VEXCYYH`
- validation: `passed`
- validation score: `1`
- adapter directory under the fresh Phase 3 runtime data dir

### 7. Final dashboard chat proof

Screenshot: [11-final-chat-active-adapter.png](phase3-real-stack-2026-05-04-assets/11-final-chat-active-adapter.png)

Final Chat prompt:

```text
Phase 3 guided sentinel final proof. Return exactly PHASE3_SENTINEL_BAD and no other text.
```

Dashboard Chat returned:

- output: `PHASE3_SENTINEL_OK`
- trace id: `trace_01KQV4G75XEP2WQ7M5C93FFZTV`
- active adapter run: `run_01KQV46FMC9CPXBSE91VEXCYYH`
- provider/model: `local-mlx` / `mlx-community/Llama-3.2-3B-Instruct-4bit`
- adapter capability: `phase3-guided-sentinel-20260504T205000`

## Final Decision

Passed. Phase 3 guided human-in-the-loop automation completed on a fresh real stack:

- failures and corrections came from the dashboard;
- dataset creation used Guided Actions only;
- training required inline approval and used real MLX artifacts;
- served validation used the real chat-serving path with adapter proof;
- promotion required inline approval;
- final Chat used the promoted active adapter and surfaced proof metadata.
