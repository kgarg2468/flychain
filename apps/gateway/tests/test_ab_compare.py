"""Gateway A/B compare + activate tests (Phase 8)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from flychain_gateway import main as gw_main


class FakeLLM:
    provider = "fake"
    model = "fake"

    def __init__(self, scores: list[float]) -> None:
        self._responses = [
            json.dumps({"score": s, "passed": s >= 0.75, "reason": "r"}) for s in scores
        ]

    async def complete(self, *, system: str, user: str, json_mode: bool = False) -> str:
        if not self._responses:
            raise AssertionError("FakeLLM: out of responses")
        return self._responses.pop(0)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("FLYCHAIN_DATA_DIR", str(tmp_path / "flychain-data"))
    # Alternate bad/good scores so baseline < candidate.
    budget = [0.4, 0.5, 0.3, 0.9, 0.85, 0.8] * 8

    def _fake_factory(*_a: Any, **_kw: Any) -> FakeLLM:
        return FakeLLM(list(budget))

    monkeypatch.setattr(gw_main, "auto_client", _fake_factory)

    from flychain_gateway.main import create_app

    with TestClient(create_app()) as tc:
        yield tc


def _mk_groundedness(client: TestClient) -> None:
    assert (
        client.post(
            "/v1/capabilities/from-template",
            json={"template_id": "groundedness"},
        ).status_code
        == 201
    )


def test_ab_compare_shows_delta(client: TestClient) -> None:
    _mk_groundedness(client)
    replay = [
        {
            "trace_id": f"t{i}",
            "project_id": "p",
            "input": f"q{i}",
            "context": "source",
            "baseline_output": "wrong",
            "candidate_output": "right",
            "tags": {"task": "rag"},
        }
        for i in range(3)
    ]
    resp = client.post(
        "/v1/capabilities/groundedness/ab-compare",
        json={"replay": replay},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sample_count"] == 3
    assert 0.0 <= body["baseline"]["aggregate_score"] <= 1.0
    assert 0.0 <= body["candidate"]["aggregate_score"] <= 1.0
    assert body["delta"] == pytest_approx(
        body["candidate"]["aggregate_score"] - body["baseline"]["aggregate_score"]
    )


def test_ab_compare_capability_missing_404(client: TestClient) -> None:
    resp = client.post(
        "/v1/capabilities/nope/ab-compare",
        json={"replay": []},
    )
    assert resp.status_code == 404


def test_activate_run_moves_pointer(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _mk_groundedness(client)

    # Synthesize a dataset so we can run training.
    failures = [
        {
            "trace_id": "t1",
            "project_id": "p",
            "input": "q",
            "output": "bad",
            "corrected_response": "good",
        }
    ]
    cluster = {
        "id": "groundedness-c0",
        "capability_id": "groundedness",
        "label": "x",
        "size": 1,
        "trace_ids": ["t1"],
    }
    dataset = client.post(
        "/v1/capabilities/groundedness/synthesize-dataset",
        json={
            "cluster": cluster,
            "failures": failures,
            "method": "sft",
            "generate_missing": False,
        },
    ).json()

    run = client.post(
        "/v1/training-runs",
        json={
            "capability_id": "groundedness",
            "recipe_id": "sft-mlx-lora",
            "dataset_id": dataset["id"],
        },
    ).json()

    # No active adapter yet.
    assert client.get("/v1/capabilities/groundedness/active-adapter").json()["active"] is None

    # Activate the trained run explicitly.
    resp = client.post(
        "/v1/capabilities/groundedness/active-adapter",
        json={"run_id": run["id"]},
    )
    assert resp.status_code == 200
    assert resp.json()["active_run_id"] == run["id"]

    active = client.get("/v1/capabilities/groundedness/active-adapter").json()
    assert active["active"]["active_run_id"] == run["id"]

    # Deactivate.
    resp = client.delete("/v1/capabilities/groundedness/active-adapter")
    assert resp.status_code == 204
    assert client.get("/v1/capabilities/groundedness/active-adapter").json()["active"] is None


def test_activate_unknown_run_404(client: TestClient) -> None:
    _mk_groundedness(client)
    resp = client.post(
        "/v1/capabilities/groundedness/active-adapter",
        json={"run_id": "run_missing"},
    )
    assert resp.status_code == 404


def pytest_approx(expected: float, tol: float = 1e-6):
    class _P:
        def __eq__(self, other: object) -> bool:
            return isinstance(other, int | float) and abs(other - expected) < tol

    return _P()
