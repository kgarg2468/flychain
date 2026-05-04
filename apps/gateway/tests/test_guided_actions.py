"""Gateway guided action tests for Phase 3 human-in-the-loop automation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from flychain_capability_compiler import Cluster, ClusteringResult, SynthesizedDataset
from flychain_gateway.main import create_app
from flychain_gateway.schemas import TraceRecord
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
    original_async_client = httpx.AsyncClient

    def _dispatch(request: httpx.Request) -> httpx.Response:
        recorded["url"] = str(request.url)
        recorded["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-guided",
                "object": "chat.completion",
                "created": 0,
                "model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "GUIDED_OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
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
    with TestClient(app) as tc:
        tc.app.state.job_queue = _FakeQueue()
        tc.app.state.recorded_mlx = recorded
        yield tc


def _create_guided_capability(client: TestClient) -> None:
    resp = client.post(
        "/v1/capabilities",
        json={
            "id": "guided-sentinel",
            "name": "Guided Sentinel",
            "description": "Return the exact guided sentinel token.",
            "eval_dimensions": [
                {
                    "id": "exact_guided",
                    "description": "Must return exactly GUIDED_OK.",
                    "evaluator": {
                        "mode": "deterministic",
                        "deterministic": {
                            "type": "exact_match",
                            "expected": "GUIDED_OK",
                            "normalize": {"trim": True},
                        },
                    },
                }
            ],
            "eligible_methods": ["sft"],
            "recipe_refs": ["sft-mlx-lora-local-3b"],
        },
    )
    assert resp.status_code == 201, resp.text


def _seed_failed_trace(
    client: TestClient,
    trace_id: str,
    *,
    corrected_response: str | None = "GUIDED_OK",
) -> None:
    store = client.app.state.trace_store
    asyncio.run(
        store.insert_trace(
            TraceRecord(
                trace_id=trace_id,
                project_id="guided",
                provider="openai",
                model="gpt-4o-mini",
                method="chat.completions",
                request={"messages": [{"role": "user", "content": f"prompt {trace_id}"}]},
                response={"choices": [{"message": {"content": "GUIDED_BAD"}}]},
                capability_ids=["guided-sentinel"],
                status="ok",
                tags={"phase": "3"},
            )
        )
    )
    asyncio.run(
        store.insert_eval_scores(
            [
                {
                    "trace_id": trace_id,
                    "project_id": "guided",
                    "capability_id": "guided-sentinel",
                    "dimension": "exact_guided",
                    "score": 0.0,
                    "passed": False,
                    "reason": "wrong token",
                    "judge_model": "deterministic",
                    "evaluator_type": "deterministic",
                    "evaluator_source": "exact_match",
                }
            ]
        )
    )
    if corrected_response is not None:
        asyncio.run(
            store.insert_feedback(
                feedback_id=f"fb-{trace_id}",
                trace_id=trace_id,
                project_id="guided",
                thumb="down",
                score=-1,
                comment="wrong",
                corrected_response=corrected_response,
            )
        )


def _seed_ready_cluster(client: TestClient) -> None:
    for index in range(4):
        _seed_failed_trace(client, f"trace-{index}", corrected_response="GUIDED_OK")
    review = client.post(
        "/v1/capabilities/guided-sentinel/failures/trace-3/review",
        json={"status": "not_useful", "note": "exclude from training"},
    )
    assert review.status_code == 200, review.text
    client.app.state.cluster_store.save(
        ClusteringResult(
            capability_id="guided-sentinel",
            clusters=[
                Cluster(
                    id="guided-sentinel-c0",
                    capability_id="guided-sentinel",
                    label="guided failures",
                    size=4,
                    trace_ids=["trace-0", "trace-1", "trace-2", "trace-3"],
                )
            ],
            noise_trace_ids=[],
        )
    )


def _action(body: dict[str, Any], action_type: str) -> dict[str, Any]:
    return next(action for action in body["actions"] if action["type"] == action_type)


def test_guided_dataset_and_training_actions_are_guarded(client: TestClient) -> None:
    _create_guided_capability(client)
    _seed_ready_cluster(client)

    listed = client.get("/v1/capabilities/guided-sentinel/guided-actions")

    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["thresholds"]["min_corrected_failures"] == 3
    dataset_action = _action(body, "create_dataset")
    assert dataset_action["status"] == "available"
    assert dataset_action["requires_approval"] is False
    assert dataset_action["preview"]["included_count"] == 3
    assert dataset_action["preview"]["skipped_count"] == 1

    created = client.post(
        f"/v1/capabilities/guided-sentinel/guided-actions/{dataset_action['id']}/execute",
        json={},
    )

    assert created.status_code == 200, created.text
    created_body = created.json()
    assert created_body["result"]["row_count"] == 3
    assert created_body["result"]["included_trace_ids"] == ["trace-0", "trace-1", "trace-2"]
    assert created_body["result"]["skipped"][0]["trace_id"] == "trace-3"
    dataset_id = created_body["result"]["dataset_id"]
    dataset_path = Path(client.app.state.dataset_store.resolve_path(dataset_id))
    rows = [json.loads(line) for line in dataset_path.read_text().splitlines()]
    assert [row["trace_id"] for row in rows] == ["trace-0", "trace-1", "trace-2"]

    next_actions = client.get("/v1/capabilities/guided-sentinel/guided-actions").json()
    training_action = _action(next_actions, "start_training")
    assert training_action["status"] == "available"
    assert training_action["requires_approval"] is True
    assert training_action["preview"]["allow_backend_fallback"] is False

    blocked = client.post(
        f"/v1/capabilities/guided-sentinel/guided-actions/{training_action['id']}/execute",
        json={},
    )
    assert blocked.status_code == 409
    assert "approval" in blocked.text

    queued = client.post(
        f"/v1/capabilities/guided-sentinel/guided-actions/{training_action['id']}/execute",
        json={"approved": True},
    )

    assert queued.status_code == 200, queued.text
    assert queued.json()["result"]["run_id"].startswith("run_")
    run = client.app.state.training_run_store.load(queued.json()["result"]["run_id"])
    assert run is not None
    assert run.allow_backend_fallback is False
    assert client.app.state.job_queue.calls[-1]["function"] == "run_training_recipe"


def test_guided_served_validation_creates_managed_replay_set(client: TestClient) -> None:
    _create_guided_capability(client)
    _seed_ready_cluster(client)
    actions = client.get("/v1/capabilities/guided-sentinel/guided-actions").json()
    dataset_action = _action(actions, "create_dataset")
    dataset_id = client.post(
        f"/v1/capabilities/guided-sentinel/guided-actions/{dataset_action['id']}/execute",
        json={},
    ).json()["result"]["dataset_id"]
    dataset_path = str(client.app.state.dataset_store.resolve_path(dataset_id))
    run = TrainingRun(
        id="run_ready_validation",
        capability_id="guided-sentinel",
        recipe_id="sft-mlx-lora-local-3b",
        dataset_id=dataset_id,
        dataset_path=dataset_path,
        status="trained",
        created_at="2026-05-04T00:00:00+00:00",
        updated_at="2026-05-04T00:00:00+00:00",
        artifact={
            "backend": "mlx-lm",
            "adapter_dir": "/tmp/adapter",
            "base_model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
            "dry_run": False,
        },
        baseline={},
        candidate={},
    )
    client.app.state.training_run_store.save(run)

    validation_action = _action(
        client.get("/v1/capabilities/guided-sentinel/guided-actions").json(),
        "run_served_validation",
    )
    assert validation_action["status"] == "available"

    queued = client.post(
        f"/v1/capabilities/guided-sentinel/guided-actions/{validation_action['id']}/execute",
        json={},
    )

    assert queued.status_code == 200, queued.text
    result = queued.json()["result"]
    assert result["run_id"] == "run_ready_validation"
    assert result["replay_set_id"].startswith("replay_")
    replay = client.app.state.replay_set_store.load(result["replay_set_id"])
    assert replay is not None
    assert replay.name == f"Managed validation: {dataset_id}"
    assert replay.rows[0]["baseline_output"] == "GUIDED_BAD"
    assert replay.rows[0]["candidate_output"] == "GUIDED_OK"
    assert client.app.state.job_queue.calls[-1]["function"] == "run_served_validation"


def test_guided_promotion_requires_approval_and_returns_post_activation_proof(
    client: TestClient,
) -> None:
    _create_guided_capability(client)
    dataset_path = Path(client.app.state.training_run_store.directory) / "guided.jsonl"
    dataset_path.write_text(
        json.dumps(
            {
                "trace_id": "trace-proof",
                "messages": [
                    {"role": "user", "content": "prompt proof"},
                    {"role": "assistant", "content": "GUIDED_OK"},
                ],
                "prompt": "prompt proof",
                "completion": "GUIDED_OK",
                "capability_id": "guided-sentinel",
                "cluster_id": "guided-sentinel-c0",
            }
        )
        + "\n"
    )
    client.app.state.dataset_store.record(
        SynthesizedDataset(
            id="ds_promote",
            capability_id="guided-sentinel",
            cluster_id="guided-sentinel-c0",
            method="sft",
            path=str(dataset_path),
            row_count=1,
        )
    )
    run = TrainingRun(
        id="run_promote",
        capability_id="guided-sentinel",
        recipe_id="sft-mlx-lora-local-3b",
        dataset_id="ds_promote",
        dataset_path=str(dataset_path),
        status="validated",
        created_at="2026-05-04T00:00:00+00:00",
        updated_at="2026-05-04T00:00:00+00:00",
        artifact={
            "backend": "mlx-lm",
            "adapter_dir": "/tmp/adapter",
            "base_model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
            "dry_run": False,
        },
        baseline={"guided-sentinel": 0.0},
        candidate={"guided-sentinel": 1.0},
        served_validation={
            "status": "passed",
            "aggregate_score": 1.0,
            "sample_count": 1,
            "validation_trace_ids": ["trace-validation"],
            "provider": "local-mlx",
            "model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
            "adapter_run_id": "run_promote",
            "adapter_capability_id": "guided-sentinel",
            "routing_mode": "candidate",
            "rows": [
                {
                    "replay_trace_id": "trace-proof",
                    "input": "prompt proof",
                    "baseline_output": "GUIDED_BAD",
                    "adapted_output": "GUIDED_OK",
                    "verdict": "passed",
                }
            ],
            "failures": [],
        },
    )
    client.app.state.training_run_store.save(run)

    promote_action = _action(
        client.get("/v1/capabilities/guided-sentinel/guided-actions").json(),
        "promote_adapter",
    )
    assert promote_action["status"] == "available"
    assert promote_action["requires_approval"] is True

    blocked = client.post(
        f"/v1/capabilities/guided-sentinel/guided-actions/{promote_action['id']}/execute",
        json={},
    )
    assert blocked.status_code == 409

    promoted = client.post(
        f"/v1/capabilities/guided-sentinel/guided-actions/{promote_action['id']}/execute",
        json={"approved": True},
    )

    assert promoted.status_code == 200, promoted.text
    result = promoted.json()["result"]
    assert result["active_run_id"] == "run_promote"
    assert result["post_activation_check"]["status"] == "passed"
    assert result["post_activation_check"]["adapter_run_id"] == "run_promote"
    active = client.get("/v1/capabilities/guided-sentinel/active-adapter").json()
    assert active["active"]["active_run_id"] == "run_promote"
