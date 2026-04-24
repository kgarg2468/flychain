"""Gateway A/B compare + activate tests (Phase 8)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from flychain_gateway import main as gw_main
from flychain_gateway.training_store import TrainingRun


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


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"function": function, "args": args, "kwargs": kwargs})
        return {"job_id": f"job-{len(self.calls)}"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("FLYCHAIN_DATA_DIR", str(tmp_path / "flychain-data"))
    monkeypatch.setenv("FLYCHAIN_CLICKHOUSE_URL", "http://localhost:1/flychain")
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
    run = TrainingRun(
        id="run_trained",
        capability_id="groundedness",
        recipe_id="sft-mlx-lora",
        dataset_id="ds_demo",
        dataset_path=str(Path(client.app.state.training_run_store.directory) / "demo.jsonl"),
        status="trained",
        created_at="2026-04-22T00:00:00+00:00",
        updated_at="2026-04-22T00:00:00+00:00",
        artifact={"adapter_dir": str(Path(client.app.state.training_run_store.directory) / "adapter")},
    )
    client.app.state.training_run_store.save(run)

    # No active adapter yet.
    assert client.get("/v1/capabilities/groundedness/active-adapter").json()["active"] is None

    # Activate the trained run explicitly.
    resp = client.post(
        "/v1/capabilities/groundedness/active-adapter",
        json={"run_id": run.id},
    )
    assert resp.status_code == 200
    assert resp.json()["active_run_id"] == run.id

    active = client.get("/v1/capabilities/groundedness/active-adapter").json()
    assert active["active"]["active_run_id"] == run.id

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


def test_replay_set_crud_and_ab_compare_persists_latest_comparison(client: TestClient) -> None:
    _mk_groundedness(client)
    run = TrainingRun(
        id="run_trained",
        capability_id="groundedness",
        recipe_id="sft-mlx-lora",
        dataset_id="ds_demo",
        dataset_path="/tmp/demo.jsonl",
        status="trained",
        created_at="2026-04-22T00:00:00+00:00",
        updated_at="2026-04-22T00:00:00+00:00",
        artifact={"adapter_dir": "/tmp/adapter"},
        baseline={"groundedness": 0.4},
    )
    client.app.state.training_run_store.save(run)

    created = client.post(
        "/v1/capabilities/groundedness/replay-sets",
        json={
            "name": "held-out",
            "rows": [
                {
                    "trace_id": "t1",
                    "project_id": "p",
                    "input": "q1",
                    "context": "source",
                    "baseline_output": "wrong",
                    "candidate_output": "right",
                    "tags": {"task": "rag"},
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    replay_set = created.json()
    assert replay_set["name"] == "held-out"

    updated = client.put(
        f"/v1/capabilities/groundedness/replay-sets/{replay_set['id']}",
        json={
            "name": "held-out-v2",
            "rows": [
                {
                    "trace_id": "t1",
                    "project_id": "p",
                    "input": "q1",
                    "context": "source",
                    "baseline_output": "wrong",
                    "candidate_output": "better",
                    "tags": {"task": "rag"},
                },
                {
                    "trace_id": "t2",
                    "project_id": "p",
                    "input": "q2",
                    "context": "source",
                    "baseline_output": "wrong",
                    "candidate_output": "better",
                    "tags": {"task": "rag"},
                },
            ],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "held-out-v2"

    listed = client.get("/v1/capabilities/groundedness/replay-sets")
    assert listed.status_code == 200
    assert len(listed.json()["replay_sets"]) == 1

    compared = client.post(
        "/v1/capabilities/groundedness/ab-compare",
        json={"run_id": run.id, "replay_set_id": replay_set["id"]},
    )
    assert compared.status_code == 200, compared.text
    body = compared.json()
    assert body["sample_count"] == 2

    saved_run = client.get(f"/v1/training-runs/{run.id}").json()
    assert saved_run["latest_comparison"]["replay_set_id"] == replay_set["id"]
    assert saved_run["latest_comparison"]["delta"] == pytest_approx(body["delta"])


def test_apply_gate_uses_latest_comparison_when_candidate_omitted(client: TestClient) -> None:
    _mk_groundedness(client)
    queue = _FakeQueue()
    client.app.state.job_queue = queue
    run = TrainingRun(
        id="run_trained",
        capability_id="groundedness",
        recipe_id="sft-mlx-lora",
        dataset_id="ds_demo",
        dataset_path="/tmp/demo.jsonl",
        status="trained",
        created_at="2026-04-22T00:00:00+00:00",
        updated_at="2026-04-22T00:00:00+00:00",
        artifact={"adapter_dir": "/tmp/adapter"},
        baseline={"groundedness": 0.4},
        latest_comparison={
            "replay_set_id": "replay_1",
            "baseline": {"aggregate_score": 0.4},
            "candidate": {"aggregate_score": 0.82},
            "delta": 0.42,
            "ts": "2026-04-22T00:00:00+00:00",
        },
    )
    client.app.state.training_run_store.save(run)

    gate = client.post(f"/v1/training-runs/{run.id}/apply-gate", json={})
    assert gate.status_code == 202, gate.text
    assert gate.json()["status"] == "gate-queued"
    assert queue.calls == [
        {
            "function": "apply_promotion_gate",
            "args": (),
            "kwargs": {
                "run_id": run.id,
                "candidate": {"groundedness": 0.82},
                "baseline": {"groundedness": 0.4},
            },
        }
    ]


def pytest_approx(expected: float, tol: float = 1e-6):
    class _P:
        def __eq__(self, other: object) -> bool:
            return isinstance(other, int | float) and abs(other - expected) < tol

    return _P()
