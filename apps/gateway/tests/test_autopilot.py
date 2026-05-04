"""Gateway Phase 4 autopilot policy tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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
    monkeypatch.setenv("FLYCHAIN_DATA_DIR", str(tmp_path / "flychain-data"))
    monkeypatch.setenv("FLYCHAIN_CLICKHOUSE_URL", "http://localhost:1/flychain")
    monkeypatch.setenv("FLYCHAIN_MLX_SERVER_URL", "http://mlx.test")
    app = create_app()
    with TestClient(app) as tc:
        tc.app.state.job_queue = _FakeQueue()
        yield tc


def _create_capability(client: TestClient) -> None:
    resp = client.post(
        "/v1/capabilities",
        json={
            "id": "autopilot-sentinel",
            "name": "Autopilot Sentinel",
            "description": "Return the exact autopilot sentinel token.",
            "eval_dimensions": [
                {
                    "id": "exact_autopilot",
                    "description": "Must return exactly AUTO_OK.",
                    "evaluator": {
                        "mode": "deterministic",
                        "deterministic": {
                            "type": "exact_match",
                            "expected": "AUTO_OK",
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
    corrected_response: str | None,
) -> None:
    store = client.app.state.trace_store
    asyncio.run(
        store.insert_trace(
            TraceRecord(
                trace_id=trace_id,
                project_id="autopilot",
                provider="openai",
                model="gpt-4o-mini",
                method="chat.completions",
                request={"messages": [{"role": "user", "content": f"prompt {trace_id}"}]},
                response={"choices": [{"message": {"content": "AUTO_BAD"}}]},
                capability_ids=["autopilot-sentinel"],
                status="ok",
                tags={"phase": "4"},
            )
        )
    )
    asyncio.run(
        store.insert_eval_scores(
            [
                {
                    "trace_id": trace_id,
                    "project_id": "autopilot",
                    "capability_id": "autopilot-sentinel",
                    "dimension": "exact_autopilot",
                    "score": 0.0,
                    "passed": False,
                    "reason": "wrong token",
                    "judge_model": "deterministic",
                    "evaluator_type": "deterministic",
                    "evaluator_source": "deterministic:exact_match",
                }
            ]
        )
    )
    if corrected_response is not None:
        asyncio.run(
            store.insert_feedback(
                feedback_id=f"fb-{trace_id}",
                trace_id=trace_id,
                project_id="autopilot",
                thumb="down",
                score=-1,
                comment="human correction",
                corrected_response=corrected_response,
            )
        )


def _seed_cluster(client: TestClient, trace_ids: list[str]) -> None:
    client.app.state.cluster_store.save(
        ClusteringResult(
            capability_id="autopilot-sentinel",
            clusters=[
                Cluster(
                    id="autopilot-sentinel-c0",
                    capability_id="autopilot-sentinel",
                    label="sentinel mismatch",
                    size=len(trace_ids),
                    trace_ids=trace_ids,
                )
            ],
            noise_trace_ids=[],
        )
    )


def _seed_validated_run(client: TestClient, *, run_id: str = "run_auto_validated") -> None:
    dataset_path = Path(client.app.state.training_run_store.directory) / "auto.jsonl"
    dataset_path.write_text(
        json.dumps(
            {
                "trace_id": "trace-proof",
                "prompt": "prompt proof",
                "completion": "AUTO_OK",
                "capability_id": "autopilot-sentinel",
                "cluster_id": "autopilot-sentinel-c0",
            }
        )
        + "\n"
    )
    client.app.state.dataset_store.record(
        SynthesizedDataset(
            id="ds_auto",
            capability_id="autopilot-sentinel",
            cluster_id="autopilot-sentinel-c0",
            method="sft",
            path=str(dataset_path),
            row_count=1,
        )
    )
    client.app.state.training_run_store.save(
        TrainingRun(
            id=run_id,
            capability_id="autopilot-sentinel",
            recipe_id="sft-mlx-lora-local-3b",
            dataset_id="ds_auto",
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
            baseline={"autopilot-sentinel": 0.0},
            candidate={"autopilot-sentinel": 1.0},
            served_validation={
                "status": "passed",
                "aggregate_score": 1.0,
                "sample_count": 1,
                "validation_trace_ids": ["trace-validation"],
                "provider": "local-mlx",
                "model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
                "adapter_run_id": run_id,
                "adapter_capability_id": "autopilot-sentinel",
                "routing_mode": "candidate",
                "rows": [],
                "failures": [],
            },
        )
    )


def test_policy_disabled_records_skipped_audit(client: TestClient) -> None:
    _create_capability(client)

    policy = client.get("/v1/capabilities/autopilot-sentinel/autopilot-policy")
    assert policy.status_code == 200, policy.text
    assert policy.json()["policy"]["enabled"] is False

    run = client.post("/v1/capabilities/autopilot-sentinel/autopilot/run", json={})

    assert run.status_code == 200, run.text
    body = run.json()
    assert body["status"] == "skipped"
    assert body["decision"]["outcome"] == "skipped"
    assert "policy disabled" in body["decision"]["reasons"]
    audit = client.get("/v1/capabilities/autopilot-sentinel/autopilot/audit").json()
    assert audit["audit"][0]["trigger"] == "manual"
    assert audit["audit"][0]["outcome"] == "skipped"


def test_human_corrections_create_dataset_and_queue_training(client: TestClient) -> None:
    _create_capability(client)
    for index in range(3):
        _seed_failed_trace(client, f"trace-{index}", corrected_response="AUTO_OK")
    _seed_cluster(client, ["trace-0", "trace-1", "trace-2"])
    client.put(
        "/v1/capabilities/autopilot-sentinel/autopilot-policy",
        json={"enabled": True},
    )

    run = client.post("/v1/capabilities/autopilot-sentinel/autopilot/run", json={})

    assert run.status_code == 200, run.text
    body = run.json()
    assert body["status"] == "running"
    assert body["decision"]["action"] == "start_training"
    assert body["decision"]["job_ids"]
    datasets = client.get("/v1/capabilities/autopilot-sentinel/flywheel").json()["datasets"]
    assert datasets[0]["row_count"] == 3
    assert datasets[0]["correction_source"] == {"human": 3, "generated": 0}
    queued = client.app.state.job_queue.calls[-1]
    assert queued["function"] == "run_training_recipe"


def test_generated_corrections_are_blocked_until_policy_allows_them(
    client: TestClient,
) -> None:
    _create_capability(client)
    for index in range(3):
        _seed_failed_trace(client, f"trace-{index}", corrected_response=None)
    _seed_cluster(client, ["trace-0", "trace-1", "trace-2"])
    client.put(
        "/v1/capabilities/autopilot-sentinel/autopilot-policy",
        json={"enabled": True, "auto_generate_corrections": True},
    )

    blocked = client.post("/v1/capabilities/autopilot-sentinel/autopilot/run", json={})

    assert blocked.status_code == 200, blocked.text
    blocked_body = blocked.json()
    assert blocked_body["status"] == "blocked"
    assert "generated corrections are not dataset eligible" in blocked_body["decision"]["reasons"]
    failures = client.get("/v1/capabilities/autopilot-sentinel/failures").json()["failures"]
    assert {row["correction_source"] for row in failures} == {"generated"}
    assert {row["dataset_eligible"] for row in failures} == {False}

    client.put(
        "/v1/capabilities/autopilot-sentinel/autopilot-policy",
        json={
            "enabled": True,
            "auto_generate_corrections": True,
            "allow_generated_corrections": True,
        },
    )
    allowed = client.post("/v1/capabilities/autopilot-sentinel/autopilot/run", json={})

    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["decision"]["action"] == "start_training"
    status = client.get("/v1/capabilities/autopilot-sentinel/autopilot").json()
    assert status["readiness"]["eligible_failures"] == 3
    datasets = client.get("/v1/capabilities/autopilot-sentinel/flywheel").json()["datasets"]
    assert datasets[0]["correction_source"] == {"human": 0, "generated": 3}


def test_promotion_requires_approval_then_rollback_disables_active_adapter(
    client: TestClient,
) -> None:
    _create_capability(client)
    _seed_validated_run(client)
    client.put(
        "/v1/capabilities/autopilot-sentinel/autopilot-policy",
        json={"enabled": True},
    )

    pending = client.post("/v1/capabilities/autopilot-sentinel/autopilot/run", json={})

    assert pending.status_code == 200, pending.text
    pending_body = pending.json()
    assert pending_body["status"] == "approval_required"
    assert pending_body["decision"]["action"] == "promote_adapter"
    decision_id = pending_body["decision"]["id"]
    assert client.get("/v1/capabilities/autopilot-sentinel/active-adapter").json()["active"] is None

    approved = client.post(
        f"/v1/capabilities/autopilot-sentinel/autopilot/approvals/{decision_id}",
        json={"approved": True},
    )

    assert approved.status_code == 200, approved.text
    assert approved.json()["active_run_id"] == "run_auto_validated"
    active = client.get("/v1/capabilities/autopilot-sentinel/active-adapter").json()["active"]
    assert active["active_run_id"] == "run_auto_validated"

    rolled_back = client.post(
        "/v1/capabilities/autopilot-sentinel/rollback",
        json={"reason": "phase4 e2e rollback"},
    )

    assert rolled_back.status_code == 200, rolled_back.text
    assert rolled_back.json()["status"] == "rolled_back"
    assert client.get("/v1/capabilities/autopilot-sentinel/active-adapter").json()["active"] is None
    audit = client.get("/v1/capabilities/autopilot-sentinel/autopilot/audit").json()["audit"]
    assert any(row["action"] == "rollback" for row in audit)


def test_auto_promote_policy_promotes_after_validation(client: TestClient) -> None:
    _create_capability(client)
    _seed_validated_run(client, run_id="run_auto_promote")
    client.put(
        "/v1/capabilities/autopilot-sentinel/autopilot-policy",
        json={
            "enabled": True,
            "auto_promote": True,
            "require_promotion_approval": False,
        },
    )

    promoted = client.post("/v1/capabilities/autopilot-sentinel/autopilot/run", json={})

    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["status"] == "complete"
    active = client.get("/v1/capabilities/autopilot-sentinel/active-adapter").json()["active"]
    assert active["active_run_id"] == "run_auto_promote"


def test_promotion_cooldown_blocks_second_auto_promotion(client: TestClient) -> None:
    _create_capability(client)
    _seed_validated_run(client, run_id="run_auto_promote")
    client.put(
        "/v1/capabilities/autopilot-sentinel/autopilot-policy",
        json={
            "enabled": True,
            "auto_promote": True,
            "require_promotion_approval": False,
        },
    )
    promoted = client.post("/v1/capabilities/autopilot-sentinel/autopilot/run", json={})
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["status"] == "complete"

    _seed_validated_run(client, run_id="run_auto_promote_next")
    blocked = client.post("/v1/capabilities/autopilot-sentinel/autopilot/run", json={})

    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["status"] == "blocked"
    assert blocked.json()["decision"]["action"] == "promote_adapter"
    assert blocked.json()["decision"]["target_id"] == "run_auto_promote_next"
    assert "promotion cooldown active" in blocked.json()["decision"]["reasons"][0]
