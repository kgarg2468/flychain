"""Gateway observability + settings tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from flychain_gateway import main as gw_main
from flychain_gateway.main import create_app
from flychain_gateway.schemas import TraceRecord


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


def test_settings_endpoint_is_env_first_and_persists_local_knobs(client: TestClient) -> None:
    resp = client.get("/v1/settings")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["openai_configured"] is False
    assert body["anthropic_configured"] is False
    assert body["settings"]["judge_model"] == "llama3.2:3b"

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
                response={"choices": [{"message": {"content": "Refunds are available for 90 days."}}]},
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
