"""Gateway training-run + promotion-gate tests (Phase 6)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from flychain_gateway.main import create_app
from flychain_gateway.training_store import TrainingRun


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("FLYCHAIN_DATA_DIR", str(tmp_path / "flychain-data"))
    monkeypatch.setenv("FLYCHAIN_CLICKHOUSE_URL", "http://localhost:1/flychain")
    app = create_app()
    with TestClient(app) as tc:
        yield tc


def _setup_dataset(client: TestClient, tmp_path_factory: Any | None = None) -> dict[str, Any]:
    """Create a capability, synthesize a small SFT dataset, and return its id."""
    resp = client.post(
        "/v1/capabilities/from-template",
        json={"template_id": "groundedness"},
    )
    assert resp.status_code == 201

    failures = [
        {
            "trace_id": "t1",
            "project_id": "p",
            "input": "q1",
            "output": "bad1",
            "corrected_response": "good1",
        },
        {
            "trace_id": "t2",
            "project_id": "p",
            "input": "q2",
            "output": "bad2",
            "corrected_response": "good2",
        },
    ]
    cluster = {
        "id": "groundedness-c0",
        "capability_id": "groundedness",
        "label": "x",
        "size": 2,
        "trace_ids": ["t1", "t2"],
    }
    resp = client.post(
        "/v1/capabilities/groundedness/synthesize-dataset",
        json={
            "cluster": cluster,
            "failures": failures,
            "method": "sft",
            "generate_missing": False,
        },
    )
    assert resp.status_code == 200
    return resp.json()


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"function": function, "args": args, "kwargs": kwargs})
        return {"job_id": f"job-{len(self.calls)}"}


def _save_trained_run(
    client: TestClient,
    *,
    capability_id: str,
    recipe_id: str,
    dataset_id: str,
    dataset_path: str,
    baseline: dict[str, float] | None = None,
) -> TrainingRun:
    run = TrainingRun(
        id="run_existing",
        capability_id=capability_id,
        recipe_id=recipe_id,
        dataset_id=dataset_id,
        dataset_path=dataset_path,
        status="trained",
        created_at="2026-04-22T00:00:00+00:00",
        updated_at="2026-04-22T00:00:00+00:00",
        artifact={
            "adapter_dir": str(Path(dataset_path).parent / "adapter"),
            "dry_run": True,
        },
        baseline=dict(baseline or {}),
        candidate={},
    )
    client.app.state.training_run_store.save(run)
    return run


def test_recipes_endpoint_lists_v1_recipes(client: TestClient) -> None:
    resp = client.get("/v1/recipes")
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()["recipes"]}
    assert {"sft-mlx-lora", "sft-unsloth-lora"} <= ids


def test_recipes_get_by_id(client: TestClient) -> None:
    resp = client.get("/v1/recipes/sft-mlx-lora")
    assert resp.status_code == 200
    assert resp.json()["backend"] == "mlx-lm"


def test_training_run_is_queued_and_enqueued(client: TestClient) -> None:
    dataset = _setup_dataset(client)
    queue = _FakeQueue()
    client.app.state.job_queue = queue
    resp = client.post(
        "/v1/training-runs",
        json={
            "capability_id": "groundedness",
            "recipe_id": "sft-unsloth-lora",  # unsloth unlikely to be available on test host
            "dataset_id": dataset["id"],
            "allow_backend_fallback": True,
        },
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["artifact"] is None
    assert len(queue.calls) == 1
    call = queue.calls[0]
    assert call["function"] == "run_training_recipe"
    assert call["args"] == ()
    assert call["kwargs"]["run_id"] == body["id"]
    assert call["kwargs"]["job_id"].startswith("job_")
    saved = client.get(f"/v1/training-runs/{body['id']}")
    assert saved.status_code == 200
    assert saved.json()["status"] == "queued"


def test_training_run_unknown_recipe_404(client: TestClient) -> None:
    dataset = _setup_dataset(client)
    resp = client.post(
        "/v1/training-runs",
        json={
            "capability_id": "groundedness",
            "recipe_id": "no-such-recipe",
            "dataset_id": dataset["id"],
        },
    )
    assert resp.status_code == 404


def test_training_run_unknown_dataset_404(client: TestClient) -> None:
    client.post("/v1/capabilities/from-template", json={"template_id": "groundedness"})
    resp = client.post(
        "/v1/training-runs",
        json={
            "capability_id": "groundedness",
            "recipe_id": "sft-mlx-lora",
            "dataset_id": "ds_missing",
        },
    )
    assert resp.status_code == 404


def test_apply_gate_is_queued(client: TestClient) -> None:
    dataset = _setup_dataset(client)
    queue = _FakeQueue()
    client.app.state.job_queue = queue
    run = _save_trained_run(
        client,
        capability_id="groundedness",
        recipe_id="sft-mlx-lora",
        dataset_id=dataset["id"],
        dataset_path=dataset["path"],
        baseline={"groundedness": 0.60},
    )

    gate_resp = client.post(
        f"/v1/training-runs/{run.id}/apply-gate",
        json={"candidate": {"groundedness": 0.72}},
    )
    assert gate_resp.status_code == 202, gate_resp.text
    body = gate_resp.json()
    assert body["status"] == "gate-queued"
    assert body["candidate"] == {"groundedness": 0.72}
    assert len(queue.calls) == 1
    call = queue.calls[0]
    assert call["function"] == "apply_promotion_gate"
    assert call["args"] == ()
    assert call["kwargs"]["job_id"].startswith("job_")
    assert call["kwargs"] == {
        "baseline": None,
        "candidate": {"groundedness": 0.72},
        "run_id": run.id,
        "job_id": call["kwargs"]["job_id"],
    }
    active = client.get("/v1/capabilities/groundedness/active-adapter").json()
    assert active["active"] is None


def test_apply_gate_rejects_ineligible_status(client: TestClient) -> None:
    dataset = _setup_dataset(client)
    run = TrainingRun(
        id="run_queued",
        capability_id="groundedness",
        recipe_id="sft-mlx-lora",
        dataset_id=dataset["id"],
        dataset_path=dataset["path"],
        status="queued",
        created_at="2026-04-22T00:00:00+00:00",
        updated_at="2026-04-22T00:00:00+00:00",
    )
    client.app.state.training_run_store.save(run)

    gate_resp = client.post(
        f"/v1/training-runs/{run.id}/apply-gate",
        json={"candidate": {"groundedness": 0.75}},
    )
    assert gate_resp.status_code == 409
