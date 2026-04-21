"""Gateway training-run + promotion-gate tests (Phase 6)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from flychain_gateway.main import create_app


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


def test_recipes_endpoint_lists_v1_recipes(client: TestClient) -> None:
    resp = client.get("/v1/recipes")
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()["recipes"]}
    assert {"sft-mlx-lora", "sft-unsloth-lora"} <= ids


def test_recipes_get_by_id(client: TestClient) -> None:
    resp = client.get("/v1/recipes/sft-mlx-lora")
    assert resp.status_code == 200
    assert resp.json()["backend"] == "mlx-lm"


def test_training_run_uses_dry_run_when_backend_unavailable(client: TestClient) -> None:
    dataset = _setup_dataset(client)
    resp = client.post(
        "/v1/training-runs",
        json={
            "capability_id": "groundedness",
            "recipe_id": "sft-unsloth-lora",  # unsloth unlikely to be available on test host
            "dataset_id": dataset["id"],
            "allow_backend_fallback": True,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "trained"
    assert body["artifact"]["dry_run"] is True
    adapter_json = Path(body["artifact"]["adapter_dir"]) / "adapter.json"
    assert adapter_json.exists()
    data = json.loads(adapter_json.read_text())
    assert data["recipe_id"] == "sft-unsloth-lora"


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


def test_apply_gate_promotes_and_sets_active_adapter(client: TestClient) -> None:
    dataset = _setup_dataset(client)
    run = client.post(
        "/v1/training-runs",
        json={
            "capability_id": "groundedness",
            "recipe_id": "sft-mlx-lora",
            "dataset_id": dataset["id"],
            "baseline": {"groundedness": 0.60},
        },
    ).json()

    gate_resp = client.post(
        f"/v1/training-runs/{run['id']}/apply-gate",
        json={"candidate": {"groundedness": 0.72}},
    )
    assert gate_resp.status_code == 200, gate_resp.text
    body = gate_resp.json()
    assert body["verdict"]["decision"] == "promote"
    assert body["run"]["status"] == "promoted"

    active = client.get("/v1/capabilities/groundedness/active-adapter").json()
    assert active["active"] is not None
    assert active["active"]["active_run_id"] == run["id"]


def test_apply_gate_archives_on_small_delta(client: TestClient) -> None:
    dataset = _setup_dataset(client)
    run = client.post(
        "/v1/training-runs",
        json={
            "capability_id": "groundedness",
            "recipe_id": "sft-mlx-lora",
            "dataset_id": dataset["id"],
            "baseline": {"groundedness": 0.60},
        },
    ).json()

    gate_resp = client.post(
        f"/v1/training-runs/{run['id']}/apply-gate",
        json={"candidate": {"groundedness": 0.61}},  # +0.01 below 0.05 threshold
    )
    assert gate_resp.status_code == 200
    body = gate_resp.json()
    assert body["verdict"]["decision"] == "archive"
    assert body["run"]["status"] == "archived"
    active = client.get("/v1/capabilities/groundedness/active-adapter").json()
    assert active["active"] is None


def test_dpo_recipe_end_to_end(client: TestClient) -> None:
    """A DPO recipe runs through the dry-run backend when mlx-lm isn't available."""
    # Create a groundedness capability and synthesize a DPO dataset.
    client.post("/v1/capabilities/from-template", json={"template_id": "groundedness"})
    failures = [
        {
            "trace_id": "t1",
            "project_id": "p",
            "input": "q1",
            "output": "bad1",
            "corrected_response": "good1",
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
            "method": "dpo",
            "generate_missing": False,
        },
    ).json()

    run = client.post(
        "/v1/training-runs",
        json={
            "capability_id": "groundedness",
            "recipe_id": "dpo-mlx-lora",
            "dataset_id": dataset["id"],
            "baseline": {"groundedness": 0.55},
        },
    ).json()
    assert run["status"] == "trained"
    assert run["recipe_id"] == "dpo-mlx-lora"
    import json as _json
    from pathlib import Path as _P

    adapter_json = _P(run["artifact"]["adapter_dir"]) / "adapter.json"
    payload = _json.loads(adapter_json.read_text())
    assert payload["recipe_id"] == "dpo-mlx-lora"


def test_apply_gate_archives_on_regression(client: TestClient) -> None:
    """A regression on another tracked capability beyond tolerance archives."""
    dataset = _setup_dataset(client)
    run = client.post(
        "/v1/training-runs",
        json={
            "capability_id": "groundedness",
            "recipe_id": "sft-mlx-lora",
            "dataset_id": dataset["id"],
            "baseline": {"groundedness": 0.60, "instruction-following": 0.80},
        },
    ).json()

    gate_resp = client.post(
        f"/v1/training-runs/{run['id']}/apply-gate",
        json={
            "candidate": {
                "groundedness": 0.75,
                "instruction-following": 0.70,  # -0.10 > 0.02 tolerance
            }
        },
    )
    body = gate_resp.json()
    assert body["verdict"]["decision"] == "archive"
    assert any(
        r["capability_id"] == "instruction-following" for r in body["verdict"]["regressions"]
    )
