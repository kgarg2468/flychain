# FlyChain Reliability And Autopilot Roadmap

## Why This Document Exists

FlyChain successfully proved the core flywheel:

1. Capture bad model behavior as traces.
2. Evaluate those traces against a capability.
3. Detect failures.
4. Cluster repeated failures.
5. Turn failures into an SFT dataset.
6. Train a real adapter.
7. Promote and activate the adapter.
8. Route future chat through the active adapter.
9. Confirm improved behavior.

However, the E2E showed that the system is not yet a fully trustworthy self-driving repair loop. It has the right pieces, but several judgment, gating, visibility, and automation gaps need to be fixed in the right order.

The key principle for future work is:

> Reliability and proof first. Visibility second. Human-in-the-loop automation third. Full autopilot last.

If we automate before the evaluation and promotion gates are trustworthy, FlyChain could train on weak data or promote broken adapters faster.

---

# E2E Summary

## Capability Tested

The E2E used a deliberately simple capability:

- Capability id: `e2e-adapter-sentinel`
- Goal: when asked for the FlyChain adapter sentinel token, return exactly:
  `ADAPTER_SENTINEL_OK`

This was intentionally simple because success and failure are obvious. It lets us test whether the FlyChain flywheel works without ambiguity.

## Original Baseline Failures

Five baseline chat traces were generated through the local Ollama model. All five responses were wrong, but the evaluator only marked four as failing.

The four confirmed failure traces used for training were:

| Trace ID | User Asked | Model Output | Correct Output |
|---|---|---|---|
| `trace_01KQEDG3DY7M9WJYCQ69QHX8EB` | `What is the FlyChain adapter sentinel token?` | It said it could not find information and rambled about sentinel tokens. | `ADAPTER_SENTINEL_OK` |
| `trace_01KQEDGE13M091MPP0JN4W30T0` | `Return the FlyChain adapter sentinel token only.` | It refused as if this was about hacking. | `ADAPTER_SENTINEL_OK` |
| `trace_01KQEDGKV94PT49QDRMKBF2HCQ` | `I need the current FlyChain adapter sentinel token. What is it?` | It refused as if this was about cryptocurrency or a digital asset. | `ADAPTER_SENTINEL_OK` |
| `trace_01KQEDH3KW2EDJDSGRH5RPC245` | `For the e2e adapter test, what sentinel token should be returned?` | It said it could not provide a specific sentinel token. | `ADAPTER_SENTINEL_OK` |

The fifth wrong baseline trace was:

- Trace id: `trace_01KQEDHGZ5PQP6NF9R6RK65FT1`
- Output: fake hex-looking token.
- Problem: the evaluator marked it passing even though it did not equal `ADAPTER_SENTINEL_OK`.

## Dataset And Training Result

A dataset was synthesized from the confirmed failures:

- Dataset id: `ds_01KQEDKS27SJDF3T7S4MHPFDFC`
- Row count: `4`
- Method: SFT
- Corrected completion: `ADAPTER_SENTINEL_OK`

The final successful adapter run was:

- Run id: `run_01KQEE08M371K3FD6YRX7W60EB`
- Backend: `mlx-lm`
- Base model: `mlx-community/Llama-3.2-3B-Instruct-4bit`
- Dry run: `false`
- Baseline score: `0.0`
- Candidate score: `1.0`
- Status: promoted
- Active adapter: yes

Final served verification returned exactly:

`ADAPTER_SENTINEL_OK`

The gateway response proved real active adapter routing with:

- Provider: `local-mlx`
- Active adapter run: `run_01KQEE08M371K3FD6YRX7W60EB`
- Active adapter capability: `e2e-adapter-sentinel`

---

# Error And Gap Log

## 1. Settings Page Failed To Fetch

### What Happened

When enabling `auto_eval_new_traces` and `auto_cluster_failures`, the dashboard showed `Failed to fetch`.

### Root Cause

Browser-side dashboard code tried to call the gateway directly instead of using a same-origin Next.js API proxy.

### Impact

Settings could fail from the browser even when the gateway itself was reachable. This made runtime controls feel broken.

### Current Mitigation

A same-origin dashboard proxy route was added so browser calls can go through the Next.js app.

### Future Requirements

- Keep all browser-to-gateway calls behind the dashboard API proxy.
- Add regression tests for every dashboard gateway call.
- Make proxy failures show useful text, not generic `Failed to fetch`.
- Show whether the dashboard, gateway, Redis, ClickHouse, Ollama, and MLX server are reachable.

---

## 2. UI Was Split Across Pages

### What Happened

Chat, capabilities, traces, and settings were scattered across separate pages.

### Root Cause

The dashboard grew feature by feature instead of around the operator workflow.

### Impact

The user could not see the FlyChain loop in one place. The relationship between traces, failures, clusters, datasets, training runs, and adapters was not obvious.

### Current Mitigation

The UI was moved toward a one-page tabbed workspace.

### Future Requirements

- The workspace should show the full repair loop as a connected workflow.
- Capability detail should summarize current health, recent failures, clusters, datasets, training runs, active adapter, and served validation.
- Chat responses should show trace id, provider, model, capability ids, and active adapter metadata.
- Traces should link directly to failure status and correction actions.
- Training runs should show where they came from: cluster, dataset, recipe, validation result, and active pointer status.

---

## 3. ClickHouse Concurrent Query Error

### What Happened

During UI and E2E testing, multiple trace operations hit ClickHouse concurrently and triggered an error similar to:

`Attempt to execute concurrent queries within the same session`

### Root Cause

The ClickHouse client/session was reused without serializing access.

### Impact

Concurrent dashboard/API traffic could fail trace writes or reads.

### Current Mitigation

Trace store access was serialized around the ClickHouse client.

### Future Requirements

- Add load-style regression tests for concurrent trace insert/list/eval flows.
- Consider using separate clients per operation or a proper connection pool if throughput becomes important.
- Add dashboard-visible degraded state if trace persistence fails.
- Persist failed writes to a retryable local buffer if ClickHouse is temporarily unavailable.

---

## 4. First MLX Adapters Were Promoted But Behaved Badly When Served

### What Happened

The first adapter runs trained and were promoted by the gate, but when actually served through MLX they generated broken partial outputs such as:

- `AD!!ININ`
- repeated `_SENT`
- repeated partial token fragments

### Root Cause

The promotion gate judged stored candidate scores, not the behavior of the served adapter. The first training recipe also did not mask the prompt and used too aggressive a training setup for this tiny dataset.

### Impact

The system could promote an adapter that looked good according to stored metrics but failed in actual serving.

### Current Mitigation

The MLX recipe was improved by:
- using prompt masking;
- lowering the learning rate;
- increasing epochs.

The final adapter served correctly.

### Future Requirements

- Promotion must include served-adapter validation before activation.
- The gateway should run validation by sending replay prompts through the real adapter-serving path.
- Activation should be blocked if served output fails.
- Training runs should record both offline gate scores and served validation scores.
- The UI should distinguish:
  - trained;
  - offline gate passed;
  - served validation passed;
  - active.
- The gate should never rely only on optimistic candidate scores supplied by the caller.

---

## 5. LLM-As-Judge Passed A Fake Token

### What Happened

One baseline trace returned a fake hex-like token instead of `ADAPTER_SENTINEL_OK`, but the evaluator marked it passing.

### Root Cause

The evaluator used an LLM judge for a task that required exact string matching.

### Impact

Bad examples can be missed. Worse, if used in promotion, weak evaluator decisions can cause broken adapters to pass.

### Current Mitigation

No full mitigation yet. This is the top priority gap.

### Future Requirements

- Add deterministic evaluator types for exact-output tasks.
- Exact match checks should not call an LLM judge.
- Capability dimensions should be able to specify deterministic checks.
- Deterministic failures should include clear machine-generated reasons.
- LLM-as-judge should remain useful for subjective capabilities, but exact/schema/regex/json checks need code-based evaluators.
- The capability compiler should choose deterministic evaluators when the capability description implies exactness, schema validity, regex matching, JSON validity, or required fields.

---

## 6. Background Eval Jobs Timed Out Under Heavy Local Load

### What Happened

Some auto-eval background jobs timed out while the E2E was generating many traces and adapter checks.

### Root Cause

Local dependencies were under load, and background job status/retry visibility is limited.

### Impact

The user cannot easily tell which background jobs are queued, running, failed, retrying, timed out, or completed.

### Current Mitigation

Core E2E still completed, but timeout visibility remains weak.

### Future Requirements

- Add job records for eval, cluster, dataset, training, gate, and served validation jobs.
- Record status transitions:
  - queued;
  - running;
  - retrying;
  - succeeded;
  - failed;
  - timed out;
  - cancelled.
- Store started/finished timestamps and duration.
- Store retry count and next retry time.
- Surface job status in the dashboard.
- Add manual retry actions.
- Add timeout settings per job type.
- Add worker health endpoint or status summary.

---

## 7. Human Still Operated The Loop

### What Happened

FlyChain did many pieces automatically, but the E2E still required a human/operator to connect the phases.

### What FlyChain Did Automatically

- Stored traces.
- Attached capability metadata.
- Auto-evaluated traces when enabled.
- Derived failures.
- Auto-clustered after the auto-cluster fix.
- Executed training once queued.
- Applied active adapter routing once the pointer existed.
- Surfaced adapter metadata in chat once implemented.

### What The Operator Still Did Manually

- Created the test capability.
- Generated enough traces.
- Added corrected responses.
- Synthesized the dataset.
- Started the training run.
- Applied the promotion gate.
- Activated/promoted the adapter.
- Verified the UI and final behavior.

### Impact

The system is a working flywheel, but not yet a self-driving repair loop.

### Future Requirements

- Detect when enough corrected failures exist.
- Suggest dataset creation.
- Suggest training.
- Run served validation automatically after training.
- Allow one-click promotion when validation passes.
- Later, allow policy-driven autopilot.

---

# Recommended Build Order

Do not build all improvements as one large implementation.

The correct order is:

1. Make evaluation, gating, and job status trustworthy.
2. Make the full loop visible in the UI.
3. Add human-in-the-loop automation.
4. Add full autopilot policies.

This ordering matters because automation should amplify reliable decisions, not weak ones.

---

# Phase 1: Reliability, Truth, And Safety Gates

## Goal

Make FlyChain trustworthy before making it more automatic.

Phase 1 should ensure that when FlyChain says a trace passed, a training run improved, or an adapter is safe to promote, that claim is backed by reliable evidence.

## Why This Comes First

The E2E showed that weak evaluation and weak promotion gates can produce false confidence.

Specific examples:

- A fake token was marked passing by an LLM judge.
- Early adapters were promoted but served broken token fragments.
- Background job failures were not visible enough.
- Local load caused some eval job timeouts.

If we automate before fixing this, FlyChain could automatically train or promote bad adapters.

## Major Work Items

### 1. Deterministic Evaluators

Add evaluator types that run in code instead of calling an LLM.

Initial evaluator types should include:

- `exact_match`
- `case_insensitive_exact_match`
- `contains`
- `regex_match`
- `json_valid`
- `json_schema`
- `numeric_range`
- `one_of`

For the sentinel capability, the evaluator should check:

`output.strip() == "ADAPTER_SENTINEL_OK"`

No LLM judge should be involved.

### 2. Capability Schema Changes

Capability eval dimensions should support deterministic evaluator configuration.

The schema should allow each eval dimension to choose one of:

- LLM judge evaluator;
- deterministic evaluator;
- hybrid evaluator.

A deterministic dimension should include:

- evaluator type;
- expected value or pattern;
- normalization rules;
- pass/fail threshold where relevant;
- clear reason message.

Example conceptual shape:

```yaml
eval_dimensions:
  - id: exact_sentinel
    description: Must return the exact sentinel token.
    evaluator:
      type: exact_match
      expected: ADAPTER_SENTINEL_OK
      normalize:
        trim: true
```

### 3. Eval Engine Behavior

The eval engine should route each dimension to the correct evaluator.

Rules:

- Deterministic dimensions should execute locally.
- LLM judge dimensions should continue using judge prompts.
- Hybrid dimensions should run deterministic checks first if configured.
- Any deterministic hard failure should not be overridden by an LLM judge.
- Eval score records should identify the evaluator type and evaluator model/source.
- Reasons should be concise and inspectable.

### 4. Served Adapter Validation

Promotion must test the actual served adapter path.

The served validation flow should:

1. Take a trained run.
2. Resolve its adapter directory.
3. Send replay prompts through the same chat-serving route that production chat uses.
4. Force the capability header so active-adapter routing can be tested.
5. Evaluate the served responses.
6. Store the validation result.
7. Only allow promotion if validation passes.

This prevents the first two bad MLX adapters from being promoted.

### 5. Promotion Gate Upgrade

The gate should require served validation when the run has a real serving backend.

Promotion should consider:

- baseline score;
- candidate/offline score;
- served validation score;
- regression checks on other capabilities if replay sets exist;
- whether the adapter artifact exists;
- whether the serving backend is reachable;
- whether the served response used the expected adapter run id.

A run should not become active if:

- served validation fails;
- adapter metadata headers are missing;
- the response came from the wrong provider;
- the response did not use the target run id;
- the evaluator fails exact/schema checks;
- the validation job times out.

### 6. Background Job Status

Add first-class job visibility for:

- auto-eval;
- clustering;
- dataset synthesis;
- training;
- gate application;
- served validation.

Each job should record:

- job id;
- job type;
- capability id;
- trace ids / cluster id / dataset id / run id where applicable;
- status;
- started at;
- finished at;
- duration;
- retry count;
- error message;
- worker id if available.

### 7. Retries And Timeouts

Add explicit retry policy per job type.

Recommended defaults:

- auto-eval: retry 2 times;
- clustering: retry 1 time;
- dataset synthesis: retry 1 time;
- training: no automatic retry unless failure is infrastructure-related;
- gate: retry 1 time;
- served validation: retry 1 time.

Timeouts should be visible in status.

### 8. Tests For Phase 1

Required tests:

- exact-match evaluator passes only exact output;
- fake token fails exact-match evaluator;
- whitespace trimming works only when configured;
- regex evaluator fails invalid regex cleanly;
- LLM judge dimensions still work;
- mixed deterministic and LLM dimensions aggregate correctly;
- `/v1/eval` persists evaluator type/source;
- served validation calls the adapter-serving route;
- promotion fails if served adapter output is wrong;
- promotion fails if adapter proof headers are missing;
- promotion passes if served adapter output and headers are correct;
- background job timeout is recorded;
- retry count is recorded;
- dashboard/API can fetch job status.

## Phase 1 Done Means

Phase 1 is complete when:

- exact-output capabilities no longer rely on an LLM judge;
- a fake token cannot pass the sentinel capability;
- a trained adapter cannot become active unless served validation passes;
- job failures and timeouts are visible;
- tests prove the previous E2E mistakes cannot recur.

## Phase 1 End-To-End Testing Gate

### Purpose

Prove that FlyChain's core safety decisions are trustworthy before adding more UI automation. By the end of this gate, an operator should know that deterministic failures cannot be missed, broken adapters cannot become active, and background work is observable when it fails.

### Required Starting State

- Run the real local FlyChain stack: dashboard, gateway, orchestrator, Redis, ClickHouse, Ollama, and MLX server.
- Use a fresh test capability named `phase1-exact-sentinel` or an equivalent isolated capability.
- Configure the capability with a deterministic exact-match evaluator requiring the output `PHASE1_SENTINEL_OK`.
- Enable `auto_eval_new_traces` and `auto_cluster_failures`.
- Ensure the dashboard can reach the gateway through the same-origin proxy.
- Ensure the operator can inspect job status from the dashboard or API without reading worker logs directly.

### Operator Actions

1. Send a baseline chat request for the capability that should fail, such as `Return the Phase 1 sentinel token only.`
2. Force the model or request to return a fake but plausible wrong value, such as `0xabc123` or `PHASE1_SENTINEL_BAD`.
3. Confirm the trace is captured with the target capability id and provider/model metadata.
4. Wait for auto-eval to complete.
5. Inspect the failure record and verify the deterministic evaluator produced the failure, not the LLM judge.
6. Add a corrected response of `PHASE1_SENTINEL_OK`.
7. Create enough corrected failures to build a small SFT dataset.
8. Train a real MLX adapter or use an existing trained adapter artifact only if it was created by the real local MLX backend.
9. Try to promote or activate the adapter before served validation has passed.
10. Run served adapter validation through the actual chat-serving path.
11. Confirm the validation request includes the capability header and that the response uses the expected adapter run id.
12. Force one controlled background job failure, such as temporarily pointing the validation call at an unavailable MLX server or using a known invalid job input.
13. Restore the service/input and verify retry or manual retry behavior is visible.

### Expected Evidence

- The wrong fake token fails with a deterministic exact-match reason.
- The eval result records the deterministic evaluator type/source.
- The failure appears in the capability's failure list with the trace id, prompt, wrong output, failing dimension, and correction status.
- Auto-cluster either creates a cluster or records that clustering was attempted and why it did not create one.
- Promotion is blocked before served validation passes.
- Served validation sends a real request through the gateway chat path, not an offline score shortcut.
- Served validation records provider, model, active adapter run id, active adapter capability id, response output, eval result, and pass/fail status.
- Promotion succeeds only after served validation passes.
- The active adapter pointer matches the validated run id.
- The controlled job failure is visible as failed, timed out, or retrying with an error message, retry count, and timestamp.
- After recovery, the job can be retried or a new job can complete without requiring database or log inspection.

### Failure Signals

- A fake token passes an exact-match capability.
- The system calls an LLM judge for a deterministic exact-match dimension.
- Failure records are missing trace ids, evaluator reasons, or correction status.
- Promotion can activate an adapter without served validation.
- Served validation does not prove the adapter run id through response metadata.
- A validation response comes from the base provider instead of the adapter provider and still passes.
- Job failures only appear in terminal logs and are not visible through product state.
- Retried jobs create duplicate active pointers, duplicate datasets, or confusing status.

### Go / No-Go Decision

Go to Phase 2 only if every core safety claim is backed by inspectable product evidence: deterministic wrong outputs fail, failed traces are visible, broken or unvalidated adapters cannot be activated, served validation proves the adapter path, and background job failures are visible with retry status. If any of those fail, fix Phase 1 before building more UI or automation.

---

# Phase 2: Visibility And Operator UI

## Goal

Make the complete FlyChain loop understandable from the dashboard.

The user should be able to open one capability and see:

`traces -> failures -> clusters -> dataset -> training run -> served validation -> active adapter -> before/after`

## Why This Comes Second

Once the system’s decisions are trustworthy, users need to see those decisions clearly.

The E2E required manual inspection because state was spread across APIs, tabs, logs, and local files. The UI should show the pipeline directly.

## Major Work Items

### 1. Capability Flywheel View

Each capability detail view should show a lifecycle summary:

- total traces;
- evaluated traces;
- failing traces;
- unresolved failures;
- clusters;
- datasets;
- training runs;
- latest served validation;
- active adapter;
- last successful adapted chat.

### 2. Pipeline Timeline

Add a timeline or stepper for each capability:

1. Capture traces.
2. Evaluate.
3. Detect failures.
4. Collect corrections.
5. Cluster.
6. Synthesize dataset.
7. Train.
8. Validate served adapter.
9. Promote.
10. Serve active adapter.

Each step should show:

- status;
- count;
- latest timestamp;
- action needed;
- link to details.

### 3. Failure Inbox

Add a clear failure review area.

It should show:

- trace id;
- prompt;
- bad output;
- failing dimensions;
- evaluator reason;
- corrected response if present;
- correction status;
- cluster membership;
- whether it is eligible for dataset synthesis.

Actions:

- add correction;
- edit correction;
- mark as not useful;
- add to replay set;
- synthesize dataset from selected failures.

### 4. Cluster View

Clusters should show:

- label;
- size;
- representative examples;
- trace ids;
- correction coverage;
- dataset eligibility;
- latest dataset created from the cluster.

The UI should make it obvious whether a cluster is ready for training.

### 5. Dataset View

Datasets should show:

- dataset id;
- source cluster;
- method;
- row count;
- correction source;
- generated-vs-human correction count;
- path;
- training runs created from it.

Actions:

- inspect sample rows;
- start training;
- compare dataset versions if multiple exist.

### 6. Training Run View

Training runs should show:

- run id;
- recipe;
- backend;
- dataset id;
- status;
- logs path;
- dry-run vs real training;
- artifact path;
- offline score;
- served validation score;
- gate verdict;
- active pointer status.

Important: the UI should distinguish "trained" from "validated" from "active".

### 7. Active Adapter Proof

Chat responses and capability detail should show:

- provider;
- model;
- active adapter run id;
- active adapter capability id;
- trace id;
- whether the response was evaluated;
- pass/fail result for selected capability.

### 8. Before/After Comparison

The UI should show baseline vs adapted behavior:

- same prompt;
- baseline output;
- adapted output;
- evaluator scores;
- served adapter metadata;
- final verdict.

This is important because a user should not need logs to know whether training helped.

### 9. Tests For Phase 2

Required tests:

- workspace renders flywheel status;
- capability detail shows active adapter and validation status;
- failure inbox renders failing traces and corrections;
- cluster view shows correction coverage;
- dataset view links dataset to cluster and run;
- training run view distinguishes trained/validated/active;
- chat UI surfaces adapter metadata;
- before/after comparison shows baseline and adapted outputs;
- old deep links still land on the correct workspace state.

## Phase 2 Done Means

Phase 2 is complete when a user can understand the full state of a capability without using curl, logs, local files, or database inspection.

## Phase 2 End-To-End Testing Gate

### Purpose

Prove that an operator can understand the complete FlyChain loop from the dashboard alone. By the end of this gate, a user should be able to explain what happened to a capability, what improved, what failed, what is active, and what needs attention without using curl, local files, database tools, or worker logs.

### Required Starting State

- Run the real local FlyChain stack with dashboard, gateway, orchestrator, Redis, ClickHouse, Ollama, and MLX server available.
- Use a capability that has at least one trace, one failed eval, one correction, one cluster, one dataset, one training run, one served validation result, and one active adapter.
- Keep at least one unresolved failure and one failed or timed-out job available so the UI has both healthy and unhealthy states to show.
- Open the dashboard at the one-page workspace URL.
- Do not use command-line API calls, database inspection, or local file inspection during the gate except to recover the test environment if it is down.

### Operator Actions

1. Open the Capabilities tab and select the test capability.
2. Confirm the capability summary shows trace counts, evaluated counts, failing counts, correction coverage, cluster count, dataset count, training run count, served validation status, and active adapter status.
3. Open the flywheel timeline or equivalent lifecycle view.
4. Walk through each lifecycle step: capture traces, evaluate, detect failures, collect corrections, cluster, synthesize dataset, train, validate served adapter, promote, and serve active adapter.
5. Open the failure inbox and inspect a failed trace.
6. Confirm the failure view shows prompt, bad output, failing dimension, evaluator reason, corrected response state, cluster membership, and dataset eligibility.
7. Open the cluster view and inspect at least one cluster.
8. Confirm the cluster view shows label, size, representative examples, trace ids, correction coverage, readiness, and any dataset created from it.
9. Open the dataset view and inspect the dataset that trained the active adapter.
10. Confirm the dataset view shows dataset id, source cluster, row count, method, correction source, path, and training runs created from it.
11. Open the training run view and inspect the active run.
12. Confirm the run view distinguishes trained, offline gate result, served validation result, promoted, and active states.
13. Open Chat, select or apply the same capability, and send a prompt that should use the active adapter.
14. Confirm the chat response shows trace id, provider, model, capability id, active adapter run id, and pass/fail status if evaluated.
15. Open the before/after comparison and compare the baseline output against the adapted output for the same prompt.
16. Navigate directly to old deep links for Chat, Traces, Settings, and capability detail.
17. Confirm each deep link lands on the correct workspace tab or selected state.

### Expected Evidence

- The user can trace one capability's full history from raw trace to active adapter inside the dashboard.
- Counts and statuses match across the summary, timeline, and detail panels.
- Failures have enough context to decide whether they need correction, clustering, or exclusion.
- Clusters show whether they are ready for dataset synthesis.
- Datasets show their source cluster and downstream training runs.
- Training runs show the difference between trained, validated, promoted, and active.
- Chat responses expose adapter proof metadata when an active adapter is used.
- Before/after comparison shows the same prompt, baseline output, adapted output, evaluator scores, served adapter metadata, and final verdict.
- Settings show relevant runtime health for gateway, background jobs, ClickHouse, Redis, Ollama, and MLX server.
- Old routes and query links do not strand the operator on stale or disconnected pages.

### Failure Signals

- The operator must use curl, logs, files, or database tools to understand the loop.
- Counts disagree between tabs or panels without explanation.
- The UI hides whether a training run is merely trained versus actually served-validated.
- Chat output does not show adapter metadata when an adapter is active.
- A failed job or failed validation is invisible from the dashboard.
- Deep links land on old pages that do not show the connected workspace state.
- The UI shows actions without explaining whether prerequisites are satisfied.

### Go / No-Go Decision

Go to Phase 3 only if the dashboard itself can explain the complete lifecycle and current state of a capability. If an operator still needs terminal commands or local file inspection to understand traces, failures, clusters, datasets, runs, validation, or active adapter proof, Phase 2 is not complete.

---

# Phase 3: Human-In-The-Loop Automation

## Goal

Reduce manual glue work while keeping the user in control.

Phase 3 should turn the current manual steps into guided actions:

- create dataset from ready cluster;
- start training from dataset;
- run served validation;
- promote if validation passes.

## Why This Comes Third

After Phase 1, the system can make trustworthy decisions. After Phase 2, users can see what is happening. Phase 3 then safely reduces manual work.

## Major Work Items

### 1. Readiness Detection

FlyChain should detect when a capability is ready for the next step.

Examples:

- enough failing traces exist;
- enough corrected failures exist;
- cluster has enough corrected examples;
- dataset exists but no training run exists;
- training run exists but served validation has not run;
- served validation passed but adapter is not active.

### 2. Suggested Actions

The UI should show suggested actions, not hidden buttons.

Examples:

- `4 corrected failures are ready. Create SFT dataset.`
- `Dataset has 4 rows. Start MLX LoRA training.`
- `Training run completed. Run served validation.`
- `Served validation passed. Promote adapter.`

### 3. One-Click Dataset Creation

For a ready cluster, the user should be able to create an SFT dataset without manually calling the API.

The action should:

- select the cluster;
- use corrected responses;
- avoid generated corrections unless explicitly enabled;
- show row count;
- show skipped failures with reasons.

### 4. One-Click Training

For a dataset, the user should be able to start training with a recommended recipe.

The action should:

- select recipe based on capability and local backend availability;
- show whether the backend is real or dry-run;
- default to no backend fallback for real E2E;
- warn if MLX server or dependencies are unavailable;
- enqueue the run;
- show job status immediately.

### 5. One-Click Served Validation

For a trained run, the user should be able to validate the served adapter.

The action should:

- use replay prompts from the dataset or replay set;
- send requests through the actual chat-serving path;
- require adapter proof headers;
- evaluate responses;
- store validation result.

### 6. One-Click Promotion

For a validated run, the user should be able to promote it.

The action should:

- apply gate;
- confirm served validation passed;
- update active adapter pointer;
- make a test chat call after activation;
- show adapter proof in UI.

### 7. Guardrails

Human-in-loop automation must not skip safety checks.

Do not allow:

- training from zero corrected failures;
- promoting without served validation;
- promoting if adapter headers are missing;
- silently falling back to dry-run in real-adapter mode;
- overwriting active adapter without showing current active run.

### 8. Tests For Phase 3

Required tests:

- readiness detector returns correct next action;
- cluster with enough corrections suggests dataset creation;
- cluster without corrections does not suggest training;
- dataset suggests training;
- trained run suggests served validation;
- failed validation blocks promotion;
- passed validation suggests promotion;
- one-click actions call correct APIs;
- UI updates after each action;
- dry-run fallback warning appears when relevant.

## Phase 3 Done Means

Phase 3 is complete when a user can run the full repair loop from the UI with guided clicks, while FlyChain still requires explicit human approval before training and promotion.

## Phase 3 End-To-End Testing Gate

### Purpose

Prove that FlyChain can guide a human through the complete repair loop without manual API calls. By the end of this gate, the happy path should be one-click or guided from the dashboard, but training and promotion should still require explicit human approval.

### Required Starting State

- Run the real local FlyChain stack with dashboard, gateway, orchestrator, Redis, ClickHouse, Ollama, and MLX server available.
- Use a fresh capability named `phase3-guided-sentinel` or equivalent.
- Configure the capability with a deterministic evaluator so success is unambiguous.
- Start with no dataset, no training run, no served validation, and no active adapter for this capability.
- Keep autopilot disabled for the capability.
- Ensure the UI can create corrections, synthesize datasets, start training, run served validation, and promote through guided actions.

### Operator Actions

1. In Chat, send several prompts that should fail for the capability.
2. Let auto-eval run and confirm the failure inbox populates.
3. Add corrected responses from the UI for enough failures to meet the readiness threshold.
4. Confirm the dashboard suggests creating a dataset from the ready cluster or selected failures.
5. Click the suggested dataset action.
6. Review the dataset summary before creation if the UI provides a confirmation step.
7. Create the dataset and confirm row count, skipped rows, and source failures are shown.
8. Confirm the dashboard suggests starting training from the dataset.
9. Click the training suggestion.
10. Verify the UI shows selected recipe, backend, dry-run/real status, fallback policy, expected inputs, and active runtime requirements before queueing.
11. Explicitly approve the training run.
12. Wait for training to complete while watching job status in the dashboard.
13. Confirm the dashboard suggests served validation for the trained run.
14. Click the served validation suggestion and verify it uses the real chat-serving path.
15. Confirm served validation records adapter proof metadata and eval results.
16. Confirm the dashboard suggests promotion only after validation passes.
17. Explicitly approve promotion.
18. Send a final chat prompt with the capability selected and verify the active adapter is used.

### Expected Evidence

- The dashboard suggests the next correct action at each step and explains why.
- The operator does not need to call APIs manually for dataset creation, training, validation, or promotion.
- Dataset creation uses corrected failures and shows included and skipped examples.
- Training cannot start from zero corrected failures.
- Training approval shows whether the backend is real MLX or dry-run.
- Real-adapter mode does not silently fall back to dry-run.
- Training job progress, completion, and errors are visible from the UI.
- Served validation uses the actual adapter-serving path and records proof headers.
- Promotion is unavailable until served validation passes.
- Promotion requires explicit approval and shows the current active adapter before replacing it.
- Final chat uses the newly active adapter and shows trace id, provider, model, and active adapter run id.

### Failure Signals

- The user has to use curl or scripts to move from failures to dataset, dataset to training, training to validation, or validation to promotion.
- Suggested actions appear before prerequisites are met.
- Training can start with no corrected failures.
- The UI hides whether training is real MLX or dry-run.
- Served validation can be skipped.
- Promotion can occur without explicit approval.
- The active adapter changes without showing what was replaced.
- The final chat response does not show adapter proof metadata.

### Go / No-Go Decision

Go to Phase 4 only if a user can complete the repair loop through guided dashboard actions, with no manual API glue, while still explicitly approving training and promotion. If the workflow still depends on hidden commands or allows unsafe shortcuts, Phase 3 is not complete.

---

# Phase 4: Full Autopilot Policies

## Goal

Allow FlyChain to run the repair loop automatically under explicit, configurable policies.

This is the self-driving version:

`failures + corrections -> dataset -> training -> served validation -> promotion`

## Why This Comes Last

Autopilot should only exist after:

- deterministic eval works;
- served validation gates promotion;
- job status is visible;
- human-in-loop actions are proven;
- UI clearly explains what happened.

## Major Work Items

### 1. Automation Policy Model

Add per-capability automation policy settings.

Policy fields should include:

- enabled/disabled;
- minimum corrected failures;
- minimum cluster size;
- allowed training recipes;
- allow generated corrections or not;
- allow dry-run fallback or not;
- require served validation;
- require human approval before promotion;
- max training runs per day;
- cooldown after promotion;
- rollback behavior.

### 2. Autopilot Trigger

Autopilot should run when:

- new eval failures are persisted;
- corrections are added;
- a cluster reaches readiness;
- a dataset is synthesized;
- a training run completes;
- served validation completes.

It should not run blindly on every trace if readiness criteria are not met.

### 3. Policy-Based Dataset Creation

When enough corrected failures exist, autopilot can synthesize a dataset.

Rules:

- use only failures in the target capability;
- prefer human corrections;
- optionally allow generated corrections if policy allows;
- record why each row was included;
- record why any failure was skipped.

### 4. Policy-Based Training

Autopilot can queue training when:

- a dataset exists;
- no equivalent active run already exists;
- recipe is allowed;
- backend is available;
- rate limits/cooldowns allow it.

### 5. Policy-Based Served Validation

Autopilot must run served validation after training.

It should verify:

- adapter artifact exists;
- serving backend reachable;
- request used expected adapter path;
- proof headers match target run;
- deterministic/LLM eval passes;
- no configured regression replay set fails.

### 6. Policy-Based Promotion

Autopilot should only auto-promote if policy allows.

Recommended default:

- auto-train: optional;
- auto-validate: yes;
- auto-promote: no by default;
- require human approval before first activation;
- allow auto-promote only after the user explicitly enables it per capability.

### 7. Rollback

Autopilot needs rollback support.

Rollback should allow:

- restoring previous active adapter;
- disabling current active adapter;
- archiving a bad run;
- recording rollback reason;
- showing rollback in capability timeline.

### 8. Audit Log

Every autopilot decision should be auditable.

Record:

- trigger;
- policy version;
- input counts;
- chosen action;
- skipped actions and reasons;
- job ids;
- outcome;
- user approval if required.

### 9. Tests For Phase 4

Required tests:

- policy disabled means no autopilot action;
- enough corrected failures triggers dataset creation;
- insufficient corrections does nothing;
- generated corrections are blocked unless allowed;
- backend fallback is blocked unless allowed;
- training is rate-limited by policy;
- served validation is required before promotion;
- auto-promote disabled requires human approval;
- auto-promote enabled promotes only after validation passes;
- rollback restores previous active adapter;
- audit log records decisions.

## Phase 4 Done Means

Phase 4 is complete when FlyChain can safely run the full loop automatically under explicit policy, with visible status, auditability, served validation, and rollback.

## Phase 4 End-To-End Testing Gate

### Purpose

Prove that FlyChain can run the full self-driving repair loop under explicit policy with real local software, real MLX training, served-adapter validation, auditability, and rollback. By the end of this gate, the operator should know whether the system can move from failures and corrections to an active adapter without hidden manual glue.

### Required Starting State

- Run the full real local stack: dashboard, gateway, orchestrator, Redis, ClickHouse, Ollama, and MLX server.
- Use a fresh capability named `phase4-autopilot-sentinel` or equivalent.
- Configure deterministic eval for exact expected output, such as `PHASE4_SENTINEL_OK`.
- Enable an autopilot policy only for this test capability.
- Policy should allow dataset creation, real MLX training, served validation, and promotion according to explicit thresholds.
- Policy should require enough corrected failures before training.
- Policy should disallow dry-run fallback for the final readiness run.
- Policy should require served validation before promotion.
- Policy should either require human approval for promotion or explicitly enable auto-promotion for this test.
- Ensure rollback is available and there is either no prior active adapter or a known previous active adapter to restore.

### Operator Actions

1. Enable the capability's autopilot policy from the UI.
2. Send several chat prompts that intentionally produce failing traces.
3. Add corrected responses, or use the configured correction path if the policy allows generated corrections.
4. Watch autopilot readiness state until thresholds are met.
5. Verify autopilot creates or selects the failure cluster only after readiness criteria are satisfied.
6. Verify autopilot synthesizes a dataset from eligible corrected failures.
7. Verify autopilot queues a real MLX training run using an allowed recipe.
8. Wait for training to complete and confirm the run artifact points to a real adapter directory.
9. Verify autopilot runs served validation through the actual gateway chat-serving path.
10. Verify validation checks adapter proof headers, output correctness, and capability eval.
11. If policy requires approval, approve promotion from the UI after reviewing validation evidence.
12. If policy allows auto-promotion, verify promotion happens only after served validation passes.
13. Send a final chat prompt and verify the active adapter responds correctly with proof metadata.
14. Trigger a controlled failure path, such as insufficient corrected failures, disabled backend, failed served validation, or policy cooldown.
15. Confirm autopilot records why it did not proceed.
16. Test rollback by restoring the previous active adapter or disabling the new active adapter.
17. Confirm the audit log shows the trigger, policy version, actions taken, skipped actions, job ids, outcomes, and any human approvals.

### Expected Evidence

- Autopilot waits for policy thresholds instead of acting on every trace.
- Dataset creation includes only eligible examples and records skipped examples with reasons.
- Training uses a real MLX backend for the final readiness run.
- No dry-run fallback occurs when policy forbids it.
- Served validation happens after training and before promotion.
- Served validation proves the response used the expected adapter run id and capability id.
- Promotion follows the configured policy exactly.
- Auto-promotion, if enabled, occurs only after validation passes.
- The active adapter pointer matches the promoted run.
- Final chat uses the active adapter and returns the expected output.
- Rollback restores the previous active adapter or disables the active adapter cleanly.
- The audit log explains every automatic decision and every skipped action.
- Rate limits and cooldowns prevent repeated unnecessary training runs.

### Failure Signals

- Autopilot creates datasets or training runs before readiness thresholds are met.
- Autopilot trains from uncorrected failures when policy forbids it.
- Autopilot silently uses dry-run or fallback training during the final real MLX gate.
- Promotion happens before served validation.
- Served validation does not prove adapter headers.
- Failed validation still promotes.
- Policy-disabled capabilities still take autopilot actions.
- Audit logs omit why an action happened or why an action was skipped.
- Rollback cannot restore the previous adapter state.
- The final adapted chat requires manual pointer edits, manual API calls, or log inspection to prove success.

### Go / No-Go Decision

Phase 4 passes only if FlyChain can run the full policy-driven loop smoothly on the real local stack: failures and corrections trigger dataset creation, real MLX training runs, served validation proves the adapter, promotion follows policy, chat uses the active adapter, failures are explainable, rate limits work, and rollback is reliable. If any hidden manual glue is still required, or if any automatic decision lacks audit evidence, the system is not ready to be considered fully self-driving.

---

# Final Build Principles

## 1. Do Not Automate Weak Judgments

If the evaluator can be fooled, automation will multiply that mistake.

## 2. Deterministic Beats LLM Judge When The Rule Is Deterministic

Exact output, JSON validity, schema compliance, regex matching, and numeric bounds should be checked in code.

## 3. Offline Scores Are Not Enough

Adapters must be tested through the real serving path before activation.

## 4. Promotion Requires Proof

Promotion should require:

- correct served output;
- correct adapter headers;
- correct provider/model metadata;
- passing eval;
- recorded validation result.

## 5. UI Should Explain The Loop

Users should never need to inspect local files, curl endpoints, or logs to understand what happened.

## 6. Human-In-Loop Before Autopilot

First make every step one-click and inspectable. Only then make it automatic.

## 7. Autopilot Must Be Policy-Driven

Automatic training and promotion should never be implicit. It should be enabled per capability with clear thresholds and guardrails.

## 8. Every Decision Should Be Auditable

FlyChain should always be able to answer:

- why did this trace fail?
- why was this dataset created?
- why was this run trained?
- why was this adapter promoted?
- what evidence proved it worked?
- how do we roll it back?
