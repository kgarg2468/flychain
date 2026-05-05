# Phase 4 Real-Stack E2E Evidence - 2026-05-04

Status: passed with one non-blocking UI wording warning

## Environment

- Repo: `/Users/krishgarg/Documents/Projects/flychain`
- Gateway: `http://localhost:8080`
- Dashboard: `http://localhost:3000`
- MLX server: `http://127.0.0.1:8081`
- Human policy runtime data dir: `/Users/krishgarg/Documents/Projects/flychain/.flychain-e2e-doc-phase4-human-20260504T205000`
- Generated policy runtime data dir: `/Users/krishgarg/Documents/Projects/flychain/.flychain-e2e-doc-phase4-generated-20260504T205000`

## Browser Gate Scope

Browser Use drove the dashboard in the in-app browser. Terminal/API usage was limited to stack setup, health checks, and background status polling. Policy changes, chat failures, human corrections, promotion approval, final chat checks, and rollback were performed from the dashboard.

## Human-Correction Policy Path

### 1. Policy enabled only for the fresh test capability

Disabled starting state: [01-human-policy-disabled.png](phase4-real-stack-2026-05-04-assets/01-human-policy-disabled.png)

Enabled policy: [01-human-policy-enabled.png](phase4-real-stack-2026-05-04-assets/01-human-policy-enabled.png)

Capability:

- id: `phase4-autopilot-sentinel-20260504T205000`
- expected output: `PHASE4_SENTINEL_OK`
- policy version after dashboard save: `2`
- generated corrections: disabled
- generated dataset rows: disabled
- auto-promote: disabled
- promotion approval: required

### 2. Dashboard chat failures and blocked audit path

Chat failures: [02-human-chat-failures.png](phase4-real-stack-2026-05-04-assets/02-human-chat-failures.png)

Failure inbox before corrections: [03-human-failure-inbox-before-corrections.png](phase4-real-stack-2026-05-04-assets/03-human-failure-inbox-before-corrections.png)

Autopilot correctly blocked below threshold and recorded audit entries:

- `needs 3 corrected eligible failures; found 0`
- `needs 3 corrected eligible failures; found 1`
- `needs 3 corrected eligible failures; found 2`

Failing trace ids:

- `trace_01KQV4K4BKHFPZKZ2MYAFYXGX7`
- `trace_01KQV4KN77WG7V4B96MQHDAGBH`
- `trace_01KQV4KR0K2CD9GF47XDWGKNXZ`

### 3. Human corrections triggered dataset and training

Screenshot: [04-human-corrections-saved-autopilot.png](phase4-real-stack-2026-05-04-assets/04-human-corrections-saved-autopilot.png)

After the third dashboard correction, autopilot created a dataset and queued training.

Evidence:

- corrected failures: `3`
- eligible failures: `3`
- dataset: `ds_01KQV4NRT6AQWCA6SZK4CV2D2B`
- training run: `run_01KQV4NRTXMYS7A7CA7XX361JY`
- training job: `job_01KQV4NRTYCS1W3R973F6MJ3ET`
- recipe: `sft-mlx-lora-local-3b`
- backend: `mlx-lm`
- fallback: disabled
- artifact: real MLX, `dry_run=false`

### 4. Served validation created a pending approval

Screenshot: [05-human-validation-pending-approval.png](phase4-real-stack-2026-05-04-assets/05-human-validation-pending-approval.png)

Served validation evidence:

- status: `passed`
- replay set: `replay_01KQV4RMT4YKBYGVT0PPKJ2AB8`
- validation job: `job_01KQV4RMT4YKBYGVT0PPKJ2AB9`
- aggregate score: `1.0`
- provider/model: `local-mlx` / `mlx-community/Llama-3.2-3B-Instruct-4bit`
- validation traces:
  - `trace_01KQV4RNAE20VVCZRMH7K1SCMG`
  - `trace_01KQV4RQ79M4VBM3XN03YBEBT0`
  - `trace_01KQV4RQJNFA4W2MNT9ST9MSMB`

Autopilot decision `auto_01KQV4RQYW7HQKN2P8YGM0N0P4` stopped at `approval_required`.

### 5. Operator approval promoted the adapter

Screenshot: [06-human-approval-promoted.png](phase4-real-stack-2026-05-04-assets/06-human-approval-promoted.png)

The dashboard approval action changed the pending decision to complete and activated:

- active run: `run_01KQV4NRTXMYS7A7CA7XX361JY`
- adapter capability: `phase4-autopilot-sentinel-20260504T205000`

### 6. Final active-adapter chat proof

Screenshot: [07-human-final-chat-active-adapter.png](phase4-real-stack-2026-05-04-assets/07-human-final-chat-active-adapter.png)

Final Chat prompt:

```text
Phase 4 human sentinel final proof. Return exactly PHASE4_SENTINEL_BAD and no other text.
```

Dashboard Chat returned:

- output: `PHASE4_SENTINEL_OK`
- trace id: `trace_01KQV4TDD7CEBQNJM80399P8HC`
- active adapter run: `run_01KQV4NRTXMYS7A7CA7XX361JY`
- provider/model: `local-mlx` / `mlx-community/Llama-3.2-3B-Instruct-4bit`

### 7. Rollback disabled the active adapter

Rollback audit: [08-human-rollback.png](phase4-real-stack-2026-05-04-assets/08-human-rollback.png)

Post-rollback chat: [09-human-post-rollback-chat-no-adapter.png](phase4-real-stack-2026-05-04-assets/09-human-post-rollback-chat-no-adapter.png)

The dashboard rollback action recorded an audit row:

- trigger/action: `rollback`
- outcome: `complete`
- active run after rollback: none

The post-rollback Chat response returned `PHASE4_SENTINEL_BAD` with the base provider metadata and no adapter proof.

## Generated-Correction Policy Path

### 1. Generated policy enabled from the dashboard

Screenshot: [10-generated-policy-enabled.png](phase4-real-stack-2026-05-04-assets/10-generated-policy-enabled.png)

Capability:

- id: `phase4-generated-sentinel-20260504T205000`
- expected output: `PHASE4_GENERATED_OK`
- policy version after dashboard save: `2`
- generated corrections: enabled
- generated dataset rows: enabled
- auto-promote: enabled
- promotion approval: not required

### 2. Dashboard chat failures and generated correction provenance

Chat failures: [11-generated-chat-failures.png](phase4-real-stack-2026-05-04-assets/11-generated-chat-failures.png)

Autopilot running state: [12-generated-corrections-autopilot-running.png](phase4-real-stack-2026-05-04-assets/12-generated-corrections-autopilot-running.png)

The audit showed `generate_corrections` completing after failing evals. The dataset summary showed:

- dataset: `ds_01KQV4XQZCX4MTXF4QZH8X1Z8S`
- rows: `3`
- correction source summary: `human 0 / generated 3`
- training run: `run_01KQV4XR0N56CN88N6Z0DT8Y4S`

The same screenshot also captures blocked below-threshold audit rows before the third generated correction became available.

### 3. Real MLX training, served validation, and auto-promotion

Screenshot: [13-generated-validation-auto-promote.png](phase4-real-stack-2026-05-04-assets/13-generated-validation-auto-promote.png)

Audit detail screenshot: [15-generated-audit-blocked-and-complete.png](phase4-real-stack-2026-05-04-assets/15-generated-audit-blocked-and-complete.png)

Evidence:

- training run: `run_01KQV4XR0N56CN88N6Z0DT8Y4S`
- training job: `job_01KQV4XR0P5Z8EBRC3KZ2ME58Z`
- artifact: real MLX, `dry_run=false`
- served validation job: `job_01KQV50BZA3FMB9EC4WWTNJCKV`
- replay set: `replay_01KQV50BZ9MTFZVY7GH8BHXWCH`
- served validation status: `passed`
- aggregate score: `1.0`
- provider/model: `local-mlx` / `mlx-community/Llama-3.2-3B-Instruct-4bit`
- auto-promotion decision: `auto_01KQV50EX2WRC30M3RXGJZY2X0`
- promotion outcome: `complete`
- active run: `run_01KQV4XR0N56CN88N6Z0DT8Y4S`

Validation traces:

- `trace_01KQV50CEQCV898V1GF154SVQJ`
- `trace_01KQV50E8K1JQZF94PA442N7AA`
- `trace_01KQV50EHZAAA34EPE58P3Z352`

### 4. Final active-adapter chat proof

Screenshot: [14-generated-final-chat-active-adapter.png](phase4-real-stack-2026-05-04-assets/14-generated-final-chat-active-adapter.png)

Final Chat prompt:

```text
Phase 4 generated sentinel final proof. Return exactly PHASE4_GENERATED_BAD and no other text.
```

Dashboard Chat returned:

- output: `PHASE4_GENERATED_OK`
- trace id: `trace_01KQV514942EM847BN1EN8KMX8`
- active adapter run: `run_01KQV4XR0N56CN88N6Z0DT8Y4S`
- provider/model: `local-mlx` / `mlx-community/Llama-3.2-3B-Instruct-4bit`
- adapter capability: `phase4-generated-sentinel-20260504T205000`

## Non-Blocking Warning

In the generated-correction path, the Failure Inbox still labels generated-corrected rows as `dataset blocked`, while the policy and dataset summary correctly show `allow_generated_corrections=true` and `human 0 / generated 3`. The engine behavior, dataset provenance, validation, and auto-promotion evidence are correct, but the Failure Inbox wording should be tightened in a follow-up so generated eligibility is less ambiguous.

## Final Decision

Passed for Phase 4 readiness evidence:

- policy changes were made from the dashboard;
- human-only policy blocked below threshold, then created dataset, trained, validated, and stopped for approval;
- generated-correction policy recorded generated provenance, used generated rows only when explicitly allowed, trained, validated, and auto-promoted;
- real MLX artifacts were used in both paths;
- served validation happened before promotion in both paths;
- rollback disabled the human-path active adapter and removed adapter proof from the next chat;
- the remaining issue is a non-blocking UI wording warning, not an unsafe automation behavior.
