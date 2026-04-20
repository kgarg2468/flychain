"""Gateway capability endpoint tests (Phase 3)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from flychain_gateway.main import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("FLYCHAIN_DATA_DIR", str(tmp_path / "flychain-data"))
    app = create_app()
    with TestClient(app) as tc:
        yield tc


def test_templates_endpoint_returns_five(client: TestClient) -> None:
    resp = client.get("/v1/capabilities/templates")
    assert resp.status_code == 200
    templates = resp.json()["templates"]
    assert len(templates) == 5
    ids = {t["id"] for t in templates}
    assert ids == {
        "groundedness",
        "instruction-following",
        "code-correctness",
        "uncertainty-calibration",
        "multi-step-reasoning",
    }


def test_empty_capabilities_initially(client: TestClient) -> None:
    resp = client.get("/v1/capabilities")
    assert resp.status_code == 200
    assert resp.json() == {"capabilities": []}


def test_create_from_template(client: TestClient) -> None:
    resp = client.post(
        "/v1/capabilities/from-template",
        json={"template_id": "groundedness"},
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["id"] == "groundedness"
    assert created["name"] == "Groundedness"

    listed = client.get("/v1/capabilities").json()
    assert len(listed["capabilities"]) == 1

    fetched = client.get(f"/v1/capabilities/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == "groundedness"


def test_create_from_template_rename(client: TestClient) -> None:
    resp = client.post(
        "/v1/capabilities/from-template",
        json={
            "template_id": "groundedness",
            "id": "my-groundedness",
            "name": "My Groundedness",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "my-groundedness"
    assert body["name"] == "My Groundedness"


def test_duplicate_capability_is_409(client: TestClient) -> None:
    first = client.post(
        "/v1/capabilities/from-template",
        json={"template_id": "groundedness"},
    )
    assert first.status_code == 201
    second = client.post(
        "/v1/capabilities/from-template",
        json={"template_id": "groundedness"},
    )
    assert second.status_code == 409


def test_delete_capability(client: TestClient) -> None:
    client.post("/v1/capabilities/from-template", json={"template_id": "groundedness"})
    resp = client.delete("/v1/capabilities/groundedness")
    assert resp.status_code == 204
    assert client.get("/v1/capabilities/groundedness").status_code == 404


def test_unknown_template_404(client: TestClient) -> None:
    resp = client.post(
        "/v1/capabilities/from-template",
        json={"template_id": "does-not-exist"},
    )
    assert resp.status_code == 404


def test_create_full_spec(client: TestClient) -> None:
    payload = {
        "id": "custom-1",
        "name": "Custom",
        "description": "A custom capability.",
        "eval_dimensions": [
            {
                "id": "custom_dim",
                "description": "Custom dimension.",
                "weight": 1.0,
            }
        ],
        "slice_rules": [{"type": "tag", "value": "x=1"}],
        "eligible_methods": ["sft"],
        "recipe_refs": [],
        "promotion_gate": {"threshold": 0.05, "max_other_regression": 0.02},
        "metadata": {},
    }
    resp = client.post("/v1/capabilities", json=payload)
    assert resp.status_code == 201, resp.text
    listed = client.get("/v1/capabilities").json()["capabilities"]
    assert any(s["id"] == "custom-1" for s in listed)
