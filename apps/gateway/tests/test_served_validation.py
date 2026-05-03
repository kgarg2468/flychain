"""Served adapter validation tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from flychain_gateway.main import create_app
from flychain_gateway.training_store import TrainingRun


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"function": function, "args": args, "kwargs": kwargs})
        return {"job_id": f"job-{len(self.calls)}"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    recorded: dict[str, Any] = {}
    state_ref: dict[str, Any] = {}
    original_async_client = httpx.AsyncClient

    def _dispatch(request: httpx.Request) -> httpx.Response:
        recorded["url"] = str(request.url)
        recorded["body"] = request.content.decode()
        app = state_ref.get("app")
        response_text = getattr(app.state, "mlx_response_text", "ADAPTER_SENTINEL_OK")
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-validation",
                "object": "chat.completion",
                "created": 0,
                "model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": response_text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
            },
        )

    def _ctor(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        if isinstance(kwargs.get("transport"), httpx.ASGITransport):
            return original_async_client(*args, **kwargs)
        kwargs["transport"] = httpx.MockTransport(_dispatch)
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _ctor)
    monkeypatch.setenv("FLYCHAIN_DATA_DIR", str(tmp_path / "flychain-data"))
    monkeypatch.setenv("FLYCHAIN_CLICKHOUSE_URL", "http://localhost:1/flychain")
    monkeypatch.setenv("FLYCHAIN_MLX_SERVER_URL", "http://mlx.test")

    app = create_app()
    state_ref["app"] = app
    with TestClient(app) as tc:
        tc.app.state.job_queue = _FakeQueue()
        tc.app.state.recorded_mlx = recorded
        yield tc


def _create_sentinel_capability(client: TestClient) -> None:
    resp = client.post(
        "/v1/capabilities",
        json={
            "id": "adapter-sentinel",
            "name": "Adapter Sentinel",
            "description": "Return the exact adapter sentinel token.",
            "eval_dimensions": [
                {
                    "id": "exact_sentinel",
                    "description": "Must return exactly ADAPTER_SENTINEL_OK.",
                    "evaluator": {
                        "mode": "deterministic",
                        "deterministic": {
                            "type": "exact_match",
                            "expected": "ADAPTER_SENTINEL_OK",
                            "normalize": {"trim": True},
                        },
                    },
                }
            ],
        },
    )
    assert resp.status_code == 201, resp.text


def _create_replay_set(client: TestClient) -> str:
    resp = client.post(
        "/v1/capabilities/adapter-sentinel/replay-sets",
        json={
            "name": "Sentinel validation",
            "rows": [
                {
                    "trace_id": "replay_1",
                    "project_id": "p1",
                    "input": "What is the FlyChain adapter sentinel token?",
                    "context": "",
                    "baseline_output": "wrong",
                    "candidate_output": "ADAPTER_SENTINEL_OK",
                }
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _save_real_mlx_run(client: TestClient, status: str = "trained") -> TrainingRun:
    run = TrainingRun(
        id="run_mlx_validation",
        capability_id="adapter-sentinel",
        recipe_id="sft-mlx-lora-local-3b",
        dataset_id="ds_demo",
        dataset_path="/tmp/ds.jsonl",
        status=status,
        created_at="2026-04-22T00:00:00+00:00",
        updated_at="2026-04-22T00:00:00+00:00",
        artifact={
            "backend": "mlx-lm",
            "adapter_dir": "/tmp/flychain-adapter",
            "base_model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
            "dry_run": False,
        },
        baseline={"adapter-sentinel": 0.0},
        candidate={"adapter-sentinel": 1.0},
    )
    client.app.state.training_run_store.save(run)
    return run


def test_served_validation_enqueue_records_job(client: TestClient) -> None:
    _create_sentinel_capability(client)
    replay_set_id = _create_replay_set(client)
    run = _save_real_mlx_run(client)

    resp = client.post(
        f"/v1/training-runs/{run.id}/served-validation",
        json={"replay_set_id": replay_set_id},
    )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "validation-queued"
    assert body["served_validation"]["status"] == "queued"
    assert body["served_validation"]["replay_set_id"] == replay_set_id
    job_id = body["served_validation"]["job_id"]
    assert client.app.state.job_queue.calls == [
        {
            "function": "run_served_validation",
            "args": (),
            "kwargs": {"job_id": job_id, "replay_set_id": replay_set_id, "run_id": run.id},
        }
    ]


def test_served_validation_runs_candidate_without_activating_pointer(client: TestClient) -> None:
    _create_sentinel_capability(client)
    replay_set_id = _create_replay_set(client)
    run = _save_real_mlx_run(client)

    resp = client.post(
        f"/internal/training-runs/{run.id}/served-validation/run",
        json={"replay_set_id": replay_set_id},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "passed"
    assert body["aggregate_score"] == 1.0
    assert body["provider"] == "local-mlx"
    assert body["adapter_run_id"] == run.id
    assert body["adapter_capability_id"] == "adapter-sentinel"
    assert body["routing_mode"] == "candidate"
    assert body["outputs"] == ["ADAPTER_SENTINEL_OK"]
    assert client.get("/v1/capabilities/adapter-sentinel/active-adapter").json()["active"] is None

    sent_body = json.loads(client.app.state.recorded_mlx["body"])
    assert client.app.state.recorded_mlx["url"] == "http://mlx.test/v1/chat/completions"
    assert sent_body["adapters"] == "/tmp/flychain-adapter"
    traces = client.get("/debug/traces").json()
    assert traces[-1]["method"] == "chat.completions"
    assert traces[-1]["capability_ids"] == ["adapter-sentinel"]

    saved = client.get(f"/v1/training-runs/{run.id}").json()
    assert saved["status"] == "validated"
    assert saved["served_validation"]["status"] == "passed"
    assert saved["served_validation"]["validation_trace_ids"][0].startswith("trace_")


def test_manual_activation_requires_served_validation(client: TestClient) -> None:
    _create_sentinel_capability(client)
    run = _save_real_mlx_run(client)

    blocked = client.post(
        "/v1/capabilities/adapter-sentinel/active-adapter",
        json={"run_id": run.id},
    )
    assert blocked.status_code == 409
    assert "served validation" in blocked.text

    run.served_validation = {
        "status": "passed",
        "aggregate_score": 1.0,
        "replay_set_id": "replay_ok",
    }
    run.status = "validated"
    client.app.state.training_run_store.save(run)

    activated = client.post(
        "/v1/capabilities/adapter-sentinel/active-adapter",
        json={"run_id": run.id},
    )
    assert activated.status_code == 409
    assert "served validation" in activated.text


def test_candidate_chat_route_requires_matching_capability_header(client: TestClient) -> None:
    _create_sentinel_capability(client)
    run = _save_real_mlx_run(client)

    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "local-ollama:llama3.2:3b",
            "messages": [{"role": "user", "content": "sentinel"}],
        },
        headers={"x-flychain-candidate-run-id": run.id},
    )

    assert resp.status_code == 400
    assert "x-flychain-capabilities" in resp.text


def test_candidate_chat_route_returns_adapter_proof_headers(client: TestClient) -> None:
    _create_sentinel_capability(client)
    run = _save_real_mlx_run(client)

    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "local-ollama:llama3.2:3b",
            "messages": [{"role": "user", "content": "sentinel"}],
        },
        headers={
            "x-flychain-capabilities": "adapter-sentinel",
            "x-flychain-candidate-run-id": run.id,
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.headers["x-flychain-adapter-run-id"] == run.id
    assert resp.headers["x-flychain-adapter-capability-id"] == "adapter-sentinel"
    assert resp.headers["x-flychain-adapter-routing-mode"] == "candidate"
    assert resp.headers["x-flychain-provider"] == "local-mlx"


def test_served_validation_fails_wrong_output(client: TestClient) -> None:
    client.app.state.mlx_response_text = "ADAPTER_SENTINEL_BAD"
    _create_sentinel_capability(client)
    replay_set_id = _create_replay_set(client)
    run = _save_real_mlx_run(client)

    resp = client.post(
        f"/internal/training-runs/{run.id}/served-validation/run",
        json={"replay_set_id": replay_set_id},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "failed"
    assert body["failures"][0]["scores"][0]["passed"] is False


def test_manual_activation_rejects_mismatched_validation_proof(client: TestClient) -> None:
    _create_sentinel_capability(client)
    run = _save_real_mlx_run(client)
    run.status = "validated"
    run.served_validation = {
        "status": "passed",
        "replay_set_id": "replay_ok",
        "aggregate_score": 1.0,
        "validation_trace_ids": ["trace_ok"],
        "provider": "local-mlx",
        "model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
        "adapter_run_id": "wrong_run",
        "adapter_capability_id": "adapter-sentinel",
        "routing_mode": "candidate",
        "failures": [],
    }
    client.app.state.training_run_store.save(run)

    resp = client.post(
        "/v1/capabilities/adapter-sentinel/active-adapter",
        json={"run_id": run.id},
    )

    assert resp.status_code == 409
    assert "wrong adapter run id" in resp.text
