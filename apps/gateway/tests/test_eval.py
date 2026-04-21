"""Tests for the gateway /v1/eval endpoint (Phase 4)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from flychain_gateway import main as gw_main


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


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("FLYCHAIN_DATA_DIR", str(tmp_path / "flychain-data"))
    monkeypatch.setenv("FLYCHAIN_CLICKHOUSE_URL", "http://localhost:1/flychain")

    def _fake_factory(*_args, **_kwargs):
        # Generous response budget so a request that matches multiple
        # capabilities (each with up to 3 dimensions) still has enough
        # judgements queued.
        return FakeLLM([0.9, 0.85, 0.8, 0.75, 0.95, 0.7, 0.88, 0.6, 0.92, 0.5])

    monkeypatch.setattr(gw_main, "auto_client", _fake_factory)

    from flychain_gateway.main import create_app

    with TestClient(create_app()) as tc:
        yield tc


def _mk_groundedness(client: TestClient) -> None:
    resp = client.post(
        "/v1/capabilities/from-template",
        json={"template_id": "groundedness"},
    )
    assert resp.status_code == 201, resp.text


def test_eval_returns_scores_for_matching_capability(client: TestClient) -> None:
    _mk_groundedness(client)
    resp = client.post(
        "/v1/eval",
        json={
            "trace_id": "trace_1",
            "project_id": "p1",
            "input": "What does page 12 say about onboarding?",
            "output": "Page 12 says onboarding is self-serve.",
            "context": "Page 12: Users onboard via self-serve signup.",
            "tags": {"task": "rag"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["trace_id"] == "trace_1"
    assert body["evaluated_capabilities"] == ["groundedness"]
    per = body["per_capability"]["groundedness"]
    assert 0.0 <= per["aggregate_score"] <= 1.0
    assert len(per["scores"]) == 3  # 3 eval dimensions in the template

    rows = client.get("/debug/eval-scores").json()
    assert len(rows) == 3
    assert all(r["capability_id"] == "groundedness" for r in rows)


def test_eval_skips_capabilities_that_dont_match_slice(client: TestClient) -> None:
    _mk_groundedness(client)
    resp = client.post(
        "/v1/eval",
        json={
            "trace_id": "trace_2",
            "project_id": "p1",
            "input": "hi",
            "output": "hi back",
            "tags": {"task": "chat"},  # groundedness needs task=rag
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["evaluated_capabilities"] == []
    assert body["per_capability"] == {}


def test_eval_explicit_capability_ids_404_on_unknown(client: TestClient) -> None:
    resp = client.post(
        "/v1/eval",
        json={
            "trace_id": "t",
            "project_id": "p",
            "input": "i",
            "output": "o",
            "capability_ids": ["does-not-exist"],
        },
    )
    assert resp.status_code == 404


def test_eval_runs_all_tracked_capabilities_by_default(client: TestClient) -> None:
    client.post("/v1/capabilities/from-template", json={"template_id": "groundedness"})
    client.post("/v1/capabilities/from-template", json={"template_id": "instruction-following"})
    resp = client.post(
        "/v1/eval",
        json={
            "trace_id": "t3",
            "project_id": "p1",
            "input": "output JSON with these keys: a, b",
            "output": '{"a": 1, "b": 2}',
            "context": "",
            "tags": {"task": "rag"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # Groundedness matches via tag rule; instruction-following via semantic rule (always-match in v1).
    assert set(body["evaluated_capabilities"]) == {"groundedness", "instruction-following"}
