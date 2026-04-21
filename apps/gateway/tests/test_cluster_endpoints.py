"""Gateway cluster + dataset synthesis endpoint tests (Phase 5)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from flychain_gateway import main as gw_main


class FakeLLM:
    provider = "fake"
    model = "fake"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def complete(self, *, system: str, user: str, json_mode: bool = False) -> str:
        if not self._responses:
            return json.dumps({"label": "fallback"}) if json_mode else "generated"
        return self._responses.pop(0)


class FixedEmbedder:
    provider = "fixed"
    model = "fixed"

    def __init__(self, matrix: np.ndarray) -> None:
        self._m = matrix

    async def embed(self, texts: list[str]) -> np.ndarray:
        assert len(texts) == self._m.shape[0]
        return self._m


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("FLYCHAIN_DATA_DIR", str(tmp_path / "flychain-data"))
    monkeypatch.setenv("FLYCHAIN_CLICKHOUSE_URL", "http://localhost:1/flychain")

    responses: list[Any] = [
        json.dumps({"label": "far cluster"}),
        json.dumps({"label": "near cluster"}),
    ]

    def _fake_client(*_args: Any, **_kwargs: Any) -> FakeLLM:
        return FakeLLM(responses + ["ideal A", "ideal B", "ideal C", "ideal D"] * 2)

    near = np.tile(np.array([1.0, 1.0], dtype=np.float32), (4, 1))
    near += np.random.default_rng(0).normal(0, 0.001, size=(4, 2)).astype(np.float32)
    far = np.tile(np.array([-5.0, -5.0], dtype=np.float32), (4, 1))
    far += np.random.default_rng(1).normal(0, 0.001, size=(4, 2)).astype(np.float32)
    matrix = np.vstack([near, far])

    def _fake_embedder(*_args: Any, **_kwargs: Any) -> FixedEmbedder:
        return FixedEmbedder(matrix)

    monkeypatch.setattr(gw_main, "auto_client", _fake_client)
    monkeypatch.setattr(gw_main, "auto_embedder", _fake_embedder)

    from flychain_gateway.main import create_app

    with TestClient(create_app()) as tc:
        yield tc


def _mk_groundedness(client: TestClient) -> None:
    resp = client.post(
        "/v1/capabilities/from-template",
        json={"template_id": "groundedness"},
    )
    assert resp.status_code == 201


def _failing_payload(i: int, corrected: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "trace_id": f"t{i}",
        "project_id": "p1",
        "input": f"question {i}",
        "output": f"wrong answer {i}",
    }
    if corrected is not None:
        out["corrected_response"] = corrected
    return out


def test_cluster_run_persists_and_lists(client: TestClient) -> None:
    _mk_groundedness(client)

    failures = [_failing_payload(i) for i in range(8)]
    resp = client.post(
        "/v1/capabilities/groundedness/cluster-run",
        json={"failures": failures, "min_cluster_size": 3, "summarize": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["capability_id"] == "groundedness"
    assert len(body["clusters"]) == 2

    listed = client.get("/v1/capabilities/groundedness/clusters").json()
    assert len(listed["clusters"]) == 2


def test_cluster_run_404_when_capability_missing(client: TestClient) -> None:
    resp = client.post(
        "/v1/capabilities/nope/cluster-run",
        json={"failures": [], "summarize": False},
    )
    assert resp.status_code == 404


def test_clusters_endpoint_initially_empty(client: TestClient) -> None:
    _mk_groundedness(client)
    resp = client.get("/v1/capabilities/groundedness/clusters")
    assert resp.status_code == 200
    assert resp.json()["clusters"] == []


def test_synthesize_sft_dataset_writes_jsonl(client: TestClient, tmp_path: Path) -> None:
    _mk_groundedness(client)
    failures = [
        _failing_payload(1, corrected="ideal 1"),
        _failing_payload(2, corrected="ideal 2"),
    ]
    cluster = {
        "id": "groundedness-c0",
        "capability_id": "groundedness",
        "label": "cluster",
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
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["method"] == "sft"
    assert body["row_count"] == 2
    assert body["id"].startswith("ds_")

    data_dir = tmp_path / "flychain-data" / "datasets" / "groundedness"
    files = list(data_dir.glob("ds_*.jsonl"))
    assert len(files) == 1
    lines = [json.loads(x) for x in files[0].read_text().strip().splitlines()]
    assert lines[0]["completion"] == "ideal 1"

    listed = client.get("/v1/capabilities/groundedness/datasets").json()["datasets"]
    assert len(listed) == 1
    assert listed[0]["row_count"] == 2


def test_synthesize_dpo_dataset(client: TestClient) -> None:
    _mk_groundedness(client)
    failures = [_failing_payload(1, corrected="ideal 1")]
    cluster = {
        "id": "groundedness-c0",
        "capability_id": "groundedness",
        "label": "cluster",
        "size": 1,
        "trace_ids": ["t1"],
    }
    resp = client.post(
        "/v1/capabilities/groundedness/synthesize-dataset",
        json={
            "cluster": cluster,
            "failures": failures,
            "method": "dpo",
            "generate_missing": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["method"] == "dpo"
    assert body["row_count"] == 1


def test_synthesize_unknown_method_400(client: TestClient) -> None:
    _mk_groundedness(client)
    resp = client.post(
        "/v1/capabilities/groundedness/synthesize-dataset",
        json={
            "cluster": {
                "id": "groundedness-c0",
                "capability_id": "groundedness",
                "label": "cluster",
                "size": 0,
                "trace_ids": [],
            },
            "failures": [],
            "method": "orpo",
        },
    )
    assert resp.status_code == 400
