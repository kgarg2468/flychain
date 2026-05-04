"""Gateway observability + settings tests."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from flychain_capability_compiler import Cluster, ClusteringResult, SynthesizedDataset
from flychain_gateway import main as gw_main
from flychain_gateway.main import create_app
from flychain_gateway.schemas import TraceRecord
from flychain_gateway.training_store import TrainingRun


class FakeLLM:
    provider = "fake"
    model = "fake"

    def __init__(self, scores: list[float]) -> None:
        self._responses = [
            json.dumps({"score": s, "passed": s >= 0.75, "reason": f"s={s}"}) for s in scores
        ]

    async def complete(self, *, system: str, user: str, json_mode: bool = False) -> str:
        if not self._responses:
            raise AssertionError("FakeLLM: out of responses")
        return self._responses.pop(0)


class FakeQueryResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.column_names = list(rows[0].keys()) if rows else []
        self.result_rows = [[row.get(col) for col in self.column_names] for row in rows]


class FakeClickHouseClient:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {
            "traces": [],
            "eval_scores": [],
            "feedback": [],
        }

    def ping(self) -> None:
        return None

    def close(self) -> None:
        return None

    def insert(self, table: str, rows: list[list[Any]], column_names: list[str]) -> None:
        self.tables.setdefault(table, [])
        for row in rows:
            self.tables[table].append(dict(zip(column_names, row, strict=False)))

    def query(self, sql: str) -> FakeQueryResult:
        if "FROM traces" in sql:
            table = "traces"
        elif "FROM feedback" in sql:
            table = "feedback"
        else:
            table = "eval_scores"
        return FakeQueryResult(self.tables.get(table, []))


class ConcurrentRejectingClickHouseClient(FakeClickHouseClient):
    def __init__(self) -> None:
        super().__init__()
        self._operation_lock = threading.Lock()
        self.concurrent_errors = 0

    def _enter_operation(self) -> None:
        if not self._operation_lock.acquire(blocking=False):
            self.concurrent_errors += 1
            raise RuntimeError("concurrent query on same session")

    def _leave_operation(self) -> None:
        self._operation_lock.release()

    def insert(self, table: str, rows: list[list[Any]], column_names: list[str]) -> None:
        self._enter_operation()
        try:
            time.sleep(0.05)
            super().insert(table, rows, column_names)
        finally:
            self._leave_operation()

    def query(self, sql: str) -> FakeQueryResult:
        self._enter_operation()
        try:
            time.sleep(0.05)
            return super().query(sql)
        finally:
            self._leave_operation()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("FLYCHAIN_DATA_DIR", str(tmp_path / "flychain-data"))
    monkeypatch.setenv("FLYCHAIN_CLICKHOUSE_URL", "http://localhost:1/flychain")

    def _fake_factory(*_args, **_kwargs):
        return FakeLLM([0.91, 0.82, 0.73, 0.88, 0.77, 0.66])

    monkeypatch.setattr(gw_main, "auto_client", _fake_factory)

    app = create_app()
    with TestClient(app) as tc:
        tc.app.state.trace_store._client = FakeClickHouseClient()  # noqa: SLF001
        yield tc


def test_scorecard_reads_persisted_rows_after_flush(client: TestClient) -> None:
    resp = client.post("/v1/capabilities/from-template", json={"template_id": "groundedness"})
    assert resp.status_code == 201

    resp = client.post(
        "/v1/eval",
        json={
            "trace_id": "trace_scorecard_1",
            "project_id": "proj-a",
            "input": "What does the policy say?",
            "output": "The policy says onboarding is self-serve.",
            "context": "Policy: onboarding is self-serve.",
            "tags": {"task": "rag"},
        },
    )
    assert resp.status_code == 200, resp.text

    rows = client.get("/debug/eval-scores").json()
    assert len(rows) == 3

    scorecard = client.get("/v1/capabilities/groundedness/scorecard")
    assert scorecard.status_code == 200
    body = scorecard.json()
    assert body["sample_count"] == 1
    assert body["aggregate_score"] is not None
    assert len(body["dimensions"]) == 3


def test_traces_endpoint_filters_by_project_and_capability(client: TestClient) -> None:
    store = client.app.state.trace_store
    asyncio.run(
        store.insert_trace(
            TraceRecord(
                trace_id="trace-a",
                project_id="proj-a",
                provider="openai",
                model="gpt-4o-mini",
                method="chat.completions",
                request={"messages": [{"role": "user", "content": "hi"}]},
                response={"choices": [{"message": {"content": "hello"}}]},
                status="ok",
                tags={"task": "rag"},
            )
        )
    )
    asyncio.run(
        store.insert_trace(
            TraceRecord(
                trace_id="trace-b",
                project_id="proj-b",
                provider="openai",
                model="gpt-4o-mini",
                method="chat.completions",
                request={"messages": [{"role": "user", "content": "bye"}]},
                response={"choices": [{"message": {"content": "later"}}]},
                status="error",
                error="boom",
                tags={"task": "chat"},
            )
        )
    )
    asyncio.run(
        store.insert_eval_scores(
            [
                {
                    "trace_id": "trace-a",
                    "project_id": "proj-a",
                    "capability_id": "groundedness",
                    "dimension": "all_claims_supported",
                    "score": 0.9,
                    "passed": True,
                    "reason": "ok",
                    "judge_model": "fake",
                }
            ]
        )
    )

    resp = client.get("/v1/traces?project_id=proj-a&capability_id=groundedness&status=ok")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert [t["trace_id"] for t in body["traces"]] == ["trace-a"]


def test_trace_store_serializes_clickhouse_client_operations(client: TestClient) -> None:
    store = client.app.state.trace_store
    fake = ConcurrentRejectingClickHouseClient()
    store._client = fake  # noqa: SLF001

    async def _insert_and_read() -> None:
        insert_task = asyncio.create_task(
            store.insert_trace(
                TraceRecord(
                    trace_id="trace-concurrent",
                    project_id="proj-a",
                    provider="openai",
                    model="gpt-4o-mini",
                    method="chat.completions",
                    request={"messages": [{"role": "user", "content": "hi"}]},
                    response={"choices": [{"message": {"content": "hello"}}]},
                    status="ok",
                    tags={"task": "chat"},
                )
            )
        )
        await asyncio.sleep(0.01)
        await asyncio.to_thread(store.list_traces)
        await insert_task

    asyncio.run(_insert_and_read())

    assert fake.concurrent_errors == 0


def test_settings_endpoint_is_env_first_and_persists_local_knobs(client: TestClient) -> None:
    resp = client.get("/v1/settings")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["openai_configured"] is False
    assert body["anthropic_configured"] is False
    assert body["settings"]["judge_model"] == "llama3.2:3b"
    assert {
        "Gateway",
        "Background jobs",
        "ClickHouse",
        "Redis",
        "Ollama",
        "MLX server",
    }.issubset({component["name"] for component in body["runtime"]["health"]})

    update = client.put(
        "/v1/settings",
        json={
            "judge_model": "llama3.2:1b-instruct",
            "embedding_model": "nomic-embed-text",
            "min_cluster_size": 4,
            "auto_eval_new_traces": True,
            "auto_cluster_failures": False,
        },
    )
    assert update.status_code == 200, update.text
    saved = update.json()
    assert saved["settings"]["judge_model"] == "llama3.2:1b-instruct"
    assert saved["settings"]["min_cluster_size"] == 4

    reread = client.get("/v1/settings").json()
    assert reread["settings"]["judge_model"] == "llama3.2:1b-instruct"


def test_failures_endpoint_derives_trace_eval_and_feedback_state(client: TestClient) -> None:
    resp = client.post("/v1/capabilities/from-template", json={"template_id": "groundedness"})
    assert resp.status_code == 201

    store = client.app.state.trace_store
    asyncio.run(
        store.insert_trace(
            TraceRecord(
                trace_id="trace-failure",
                project_id="proj-a",
                provider="openai",
                model="gpt-4o-mini",
                method="chat.completions",
                request={"messages": [{"role": "user", "content": "What is the refund window?"}]},
                response={
                    "choices": [{"message": {"content": "Refunds are available for 90 days."}}]
                },
                status="ok",
                tags={"task": "rag"},
            )
        )
    )
    asyncio.run(
        store.insert_eval_scores(
            [
                {
                    "trace_id": "trace-failure",
                    "project_id": "proj-a",
                    "capability_id": "groundedness",
                    "dimension": "all_claims_supported",
                    "score": 0.2,
                    "passed": False,
                    "reason": "unsupported",
                    "judge_model": "fake",
                },
                {
                    "trace_id": "trace-failure",
                    "project_id": "proj-a",
                    "capability_id": "groundedness",
                    "dimension": "no_material_omissions",
                    "score": 0.8,
                    "passed": True,
                    "reason": "fine",
                    "judge_model": "fake",
                },
            ]
        )
    )
    asyncio.run(
        store.insert_feedback(
            feedback_id="fb-1",
            trace_id="trace-failure",
            project_id="proj-a",
            thumb="down",
            score=-2,
            comment="wrong",
            corrected_response="Refunds are available for 30 days.",
        )
    )

    failures = client.get("/v1/capabilities/groundedness/failures")
    assert failures.status_code == 200, failures.text
    body = failures.json()
    assert body["capability_id"] == "groundedness"
    assert len(body["failures"]) == 1
    failure = body["failures"][0]
    assert failure["trace_id"] == "trace-failure"
    assert failure["corrected_response"] == "Refunds are available for 30 days."
    assert failure["failing_dimensions"] == ["all_claims_supported"]
    assert failure["aggregate_score"] < 1.0
    assert failure["correction_status"] == "corrected"
    assert failure["review_status"] == "needs_correction"
    assert failure["dataset_eligible"] is True
    assert failure["cluster_ids"] == []
    assert failure["dimension_results"] == [
        {
            "dimension": "all_claims_supported",
            "score": 0.2,
            "passed": False,
            "reason": "unsupported",
            "evaluator_type": "llm_judge",
            "evaluator_source": "fake",
            "ts": failure["dimension_results"][0]["ts"],
        },
        {
            "dimension": "no_material_omissions",
            "score": 0.8,
            "passed": True,
            "reason": "fine",
            "evaluator_type": "llm_judge",
            "evaluator_source": "fake",
            "ts": failure["dimension_results"][1]["ts"],
        },
    ]


def test_failure_review_marks_not_useful_without_deleting_evidence(client: TestClient) -> None:
    client.post("/v1/capabilities/from-template", json={"template_id": "groundedness"})
    store = client.app.state.trace_store
    asyncio.run(
        store.insert_trace(
            TraceRecord(
                trace_id="trace-review",
                project_id="proj-a",
                provider="openai",
                model="gpt-4o-mini",
                method="chat.completions",
                request={"messages": [{"role": "user", "content": "Q"}]},
                response={"choices": [{"message": {"content": "wrong"}}]},
                capability_ids=["groundedness"],
                status="ok",
            )
        )
    )
    asyncio.run(
        store.insert_eval_scores(
            [
                {
                    "trace_id": "trace-review",
                    "project_id": "proj-a",
                    "capability_id": "groundedness",
                    "dimension": "all_claims_supported",
                    "score": 0.0,
                    "passed": False,
                    "reason": "bad",
                    "judge_model": "fake",
                }
            ]
        )
    )

    review = client.post(
        "/v1/capabilities/groundedness/failures/trace-review/review",
        json={"status": "not_useful", "note": "duplicate noisy trace"},
    )

    assert review.status_code == 200, review.text
    assert review.json()["status"] == "not_useful"
    failures = client.get("/v1/capabilities/groundedness/failures").json()["failures"]
    assert failures[0]["trace_id"] == "trace-review"
    assert failures[0]["review_status"] == "not_useful"
    assert failures[0]["dataset_eligible"] is False
    assert failures[0]["dimension_results"][0]["reason"] == "bad"


def test_trace_eval_endpoint_returns_pending_pass_and_fail_states(client: TestClient) -> None:
    client.post("/v1/capabilities/from-template", json={"template_id": "groundedness"})
    store = client.app.state.trace_store
    for trace_id, content in [("trace-pass", "ok"), ("trace-pending", "waiting")]:
        asyncio.run(
            store.insert_trace(
                TraceRecord(
                    trace_id=trace_id,
                    project_id="proj-a",
                    provider="openai",
                    model="gpt-4o-mini",
                    method="chat.completions",
                    request={"messages": [{"role": "user", "content": content}]},
                    response={"choices": [{"message": {"content": content}}]},
                    capability_ids=["groundedness"],
                    status="ok",
                )
            )
        )
    asyncio.run(
        store.insert_eval_scores(
            [
                {
                    "trace_id": "trace-pass",
                    "project_id": "proj-a",
                    "capability_id": "groundedness",
                    "dimension": "all_claims_supported",
                    "score": 1.0,
                    "passed": True,
                    "reason": "ok",
                    "judge_model": "fake",
                }
            ]
        )
    )

    passed = client.get("/v1/traces/trace-pass/evals?capability_id=groundedness")
    pending = client.get("/v1/traces/trace-pending/evals?capability_id=groundedness")

    assert passed.status_code == 200, passed.text
    assert passed.json()["eval_status"] == "passed"
    assert passed.json()["passed"] is True
    assert passed.json()["scores"][0]["dimension"] == "all_claims_supported"
    assert pending.status_code == 200, pending.text
    assert pending.json()["eval_status"] == "pending"
    assert pending.json()["scores"] == []


def test_capability_flywheel_endpoint_summarizes_full_visibility_loop(
    client: TestClient,
) -> None:
    client.post("/v1/capabilities/from-template", json={"template_id": "groundedness"})
    store = client.app.state.trace_store
    for trace_id, output, passed in [
        ("trace-fail", "Refunds are available for 90 days.", False),
        ("trace-active", "Refunds are available for 30 days.", True),
    ]:
        asyncio.run(
            store.insert_trace(
                TraceRecord(
                    trace_id=trace_id,
                    project_id="proj-a",
                    provider="local-mlx" if trace_id == "trace-active" else "openai",
                    model="mlx-community/Llama-3.2-3B-Instruct-4bit"
                    if trace_id == "trace-active"
                    else "gpt-4o-mini",
                    method="chat.completions",
                    request={
                        "messages": [{"role": "user", "content": "What is the refund window?"}],
                        **(
                            {"adapters": "/tmp/adapter"}
                            if trace_id == "trace-active"
                            else {}
                        ),
                    },
                    response={"choices": [{"message": {"content": output}}]},
                    capability_ids=["groundedness"],
                    status="ok",
                    tags={"task": "rag"},
                )
            )
        )
        asyncio.run(
            store.insert_eval_scores(
                [
                    {
                        "trace_id": trace_id,
                        "project_id": "proj-a",
                        "capability_id": "groundedness",
                        "dimension": "all_claims_supported",
                        "score": 1.0 if passed else 0.2,
                        "passed": passed,
                        "reason": "ok" if passed else "unsupported",
                        "judge_model": "fake",
                    }
                ]
            )
        )
    asyncio.run(
        store.insert_feedback(
            feedback_id="fb-flywheel",
            trace_id="trace-fail",
            project_id="proj-a",
            thumb="down",
            score=-2,
            comment="wrong",
            corrected_response="Refunds are available for 30 days.",
        )
    )
    client.app.state.cluster_store.save(
        ClusteringResult(
            capability_id="groundedness",
            clusters=[
                Cluster(
                    id="groundedness-c0",
                    capability_id="groundedness",
                    label="refund window",
                    size=1,
                    trace_ids=["trace-fail"],
                )
            ],
            noise_trace_ids=[],
        )
    )
    client.app.state.dataset_store.record(
        SynthesizedDataset(
            id="ds_visibility",
            capability_id="groundedness",
            cluster_id="groundedness-c0",
            method="sft",
            path="/tmp/ds_visibility.jsonl",
            row_count=1,
        )
    )
    run = TrainingRun(
        id="run_visibility",
        capability_id="groundedness",
        recipe_id="sft-mlx-lora-local-3b",
        dataset_id="ds_visibility",
        dataset_path="/tmp/ds_visibility.jsonl",
        status="promoted",
        created_at="2026-05-03T00:00:00+00:00",
        updated_at="2026-05-03T00:01:00+00:00",
        artifact={"backend": "mlx-lm", "adapter_dir": "/tmp/adapter", "dry_run": False},
        baseline={"groundedness": 0.2},
        candidate={"groundedness": 1.0},
        gate_verdict={"decision": "promote", "reason": "validated"},
        latest_comparison={
            "replay_set_id": "replay_visibility",
            "baseline": {"aggregate_score": 0.2},
            "candidate": {"aggregate_score": 1.0},
            "delta": 0.8,
            "ts": "2026-05-03T00:02:00+00:00",
        },
        served_validation={
            "status": "passed",
            "aggregate_score": 1.0,
            "sample_count": 1,
            "provider": "local-mlx",
            "model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
            "adapter_run_id": "run_visibility",
            "adapter_capability_id": "groundedness",
            "rows": [
                {
                    "replay_trace_id": "trace-fail",
                    "input": "What is the refund window?",
                    "baseline_output": "Refunds are available for 90 days.",
                    "adapted_output": "Refunds are available for 30 days.",
                    "verdict": "passed",
                }
            ],
        },
    )
    client.app.state.training_run_store.save(run)
    client.app.state.adapter_pointer_store.set_active(
        "groundedness",
        run_id="run_visibility",
        adapter_dir="/tmp/adapter",
        baseline={"groundedness": 0.2},
        candidate={"groundedness": 1.0},
    )
    job = client.app.state.job_store.create(
        job_type="served_validation",
        status="failed",
        capability_id="groundedness",
        run_id="run_visibility",
    )
    client.app.state.job_store.fail(job.id, error="validation failed")

    resp = client.get("/v1/capabilities/groundedness/flywheel")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"]["total_traces"] == 2
    assert body["summary"]["evaluated_traces"] == 2
    assert body["summary"]["failing_traces"] == 1
    assert body["summary"]["unresolved_failures"] == 0
    assert body["summary"]["clusters"] == 1
    assert body["summary"]["datasets"] == 1
    assert body["summary"]["training_runs"] == 1
    assert body["summary"]["latest_served_validation"]["status"] == "passed"
    assert body["summary"]["active_adapter"]["active_run_id"] == "run_visibility"
    assert body["summary"]["last_adapted_chat"]["trace_id"] == "trace-active"
    assert [step["id"] for step in body["timeline"]] == [
        "capture",
        "evaluate",
        "fail",
        "correct",
        "cluster",
        "dataset",
        "train",
        "validate",
        "promote",
        "serve",
    ]
    assert body["failures"][0]["dimension_results"][0]["reason"] == "unsupported"
    assert body["clusters"][0]["correction_coverage"]["corrected"] == 1
    assert body["clusters"][0]["latest_dataset_id"] == "ds_visibility"
    assert body["datasets"][0]["training_run_ids"] == ["run_visibility"]
    assert body["training_runs"][0]["active"] is True
    assert body["jobs"][0]["status"] == "failed"
    assert body["before_after"]["baseline_output"] == "Refunds are available for 90 days."
    assert body["before_after"]["adapted_output"] == "Refunds are available for 30 days."
