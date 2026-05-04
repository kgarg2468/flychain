"""Smoke tests for the orchestrator worker."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from flychain_gateway.capability_store import default_data_dir
from flychain_gateway.job_store import JobStore
from flychain_gateway.training_store import AdapterPointerStore, TrainingRun, TrainingRunStore
from flychain_orchestrator import eval_client
from flychain_orchestrator.worker import (
    WorkerSettings,
    apply_promotion_gate,
    evaluate_trace,
    noop,
    run_served_validation,
    run_training_recipe,
)


@pytest.mark.asyncio
async def test_noop_returns_ok() -> None:
    result = await noop({})
    assert result == "ok"


def test_worker_settings_has_functions() -> None:
    assert noop in WorkerSettings.functions
    assert evaluate_trace in WorkerSettings.functions
    assert run_training_recipe in WorkerSettings.functions
    assert apply_promotion_gate in WorkerSettings.functions
    assert run_served_validation in WorkerSettings.functions


@pytest.mark.asyncio
async def test_evaluate_trace_calls_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, Any] = {}

    def _responder(request: httpx.Request) -> httpx.Response:
        recorded["url"] = str(request.url)
        recorded["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "trace_id": "t1",
                "evaluated_capabilities": ["groundedness"],
                "per_capability": {"groundedness": {"aggregate_score": 0.87, "scores": []}},
            },
        )

    original = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(_responder)
        return original(*args, **kwargs)

    monkeypatch.setattr(eval_client.httpx, "AsyncClient", _factory)

    result = await evaluate_trace(
        {"settings": None},
        trace_id="t1",
        project_id="p1",
        input_text="hi",
        output_text="hello",
    )
    assert result["evaluated_capabilities"] == ["groundedness"]
    assert "/v1/eval" in recorded["url"]


@pytest.mark.asyncio
async def test_evaluate_trace_records_timeout_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FLYCHAIN_DATA_DIR", str(tmp_path / "flychain-data"))
    data_dir = default_data_dir()
    job_store = JobStore(data_dir / "jobs")
    job = job_store.create(job_type="auto_eval", timeout_seconds=0)

    async def _slow_eval(**_: Any) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"trace_id": "t-timeout"}

    monkeypatch.setattr("flychain_orchestrator.worker.post_eval", _slow_eval)

    with pytest.raises(TimeoutError):
        await evaluate_trace(
            {"settings": None},
            job_id=job.id,
            trace_id="t-timeout",
            project_id="p1",
            input_text="q",
            output_text="a",
        )

    saved = job_store.load(job.id)
    assert saved is not None
    assert saved.status == "timed_out"
    assert "timed out" in str(saved.error)


@pytest.mark.asyncio
async def test_run_training_recipe_updates_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FLYCHAIN_DATA_DIR", str(tmp_path / "flychain-data"))
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = data_dir / "datasets" / "groundedness" / "demo.jsonl"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_text(json.dumps({"prompt": "x", "completion": "y"}) + "\n")

    run = TrainingRun(
        id="run_queued",
        capability_id="groundedness",
        recipe_id="sft-mlx-lora",
        dataset_id="ds_demo",
        dataset_path=str(dataset_path),
        status="queued",
        created_at="2026-04-22T00:00:00+00:00",
        updated_at="2026-04-22T00:00:00+00:00",
        artifact=None,
        baseline={"groundedness": 0.61},
        candidate={},
    )
    TrainingRunStore(data_dir / "runs").save(run)

    class _Artifact:
        def as_dict(self) -> dict[str, Any]:
            return {"adapter_dir": str(data_dir / "runs" / run.id / "artifacts"), "dry_run": True}

    class _Backend:
        def run(self, **_: Any) -> _Artifact:
            return _Artifact()

    monkeypatch.setattr(
        "flychain_orchestrator.worker.select_backend",
        lambda backend_name, allow_fallback=False: _Backend(),
    )

    result = await run_training_recipe({}, run_id=run.id)
    assert result["status"] == "trained"
    assert result["artifact"]["dry_run"] is True

    saved = TrainingRunStore(data_dir / "runs").load(run.id)
    assert saved is not None
    assert saved.status == "trained"
    assert saved.artifact is not None


@pytest.mark.asyncio
async def test_run_training_recipe_triggers_autopilot_after_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FLYCHAIN_DATA_DIR", str(tmp_path / "flychain-data"))
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = data_dir / "datasets" / "groundedness" / "demo.jsonl"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_text(json.dumps({"prompt": "x", "completion": "y"}) + "\n")
    run = TrainingRun(
        id="run_queued",
        capability_id="groundedness",
        recipe_id="sft-mlx-lora",
        dataset_id="ds_demo",
        dataset_path=str(dataset_path),
        status="queued",
        created_at="2026-04-22T00:00:00+00:00",
        updated_at="2026-04-22T00:00:00+00:00",
    )
    TrainingRunStore(data_dir / "runs").save(run)

    class _Artifact:
        def as_dict(self) -> dict[str, Any]:
            return {"adapter_dir": str(data_dir / "runs" / run.id / "artifacts"), "dry_run": True}

    class _Backend:
        def run(self, **_: Any) -> _Artifact:
            return _Artifact()

    calls: list[dict[str, str]] = []

    async def _fake_trigger(ctx: dict, *, capability_id: str, trigger: str) -> None:
        calls.append({"capability_id": capability_id, "trigger": trigger})

    monkeypatch.setattr(
        "flychain_orchestrator.worker.select_backend",
        lambda backend_name, allow_fallback=False: _Backend(),
    )
    monkeypatch.setattr("flychain_orchestrator.worker.trigger_autopilot", _fake_trigger, raising=False)

    await run_training_recipe({}, run_id=run.id)

    assert calls == [{"capability_id": "groundedness", "trigger": "training_completed"}]


@pytest.mark.asyncio
async def test_apply_promotion_gate_updates_run_and_pointer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FLYCHAIN_DATA_DIR", str(tmp_path / "flychain-data"))
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    run = TrainingRun(
        id="run_trained",
        capability_id="groundedness",
        recipe_id="sft-mlx-lora",
        dataset_id="ds_demo",
        dataset_path=str(data_dir / "datasets" / "groundedness" / "demo.jsonl"),
        status="trained",
        created_at="2026-04-22T00:00:00+00:00",
        updated_at="2026-04-22T00:00:00+00:00",
        artifact={
            "adapter_dir": str(data_dir / "runs" / "run_trained" / "artifacts"),
            "dry_run": True,
        },
        baseline={"groundedness": 0.6},
        candidate={},
    )
    TrainingRunStore(data_dir / "runs").save(run)

    result = await apply_promotion_gate(
        {},
        run_id=run.id,
        candidate={"groundedness": 0.72},
        baseline=None,
    )
    assert result["status"] == "promoted"
    assert result["gate_verdict"]["decision"] == "promote"

    saved = TrainingRunStore(data_dir / "runs").load(run.id)
    assert saved is not None
    assert saved.status == "promoted"

    active = AdapterPointerStore(data_dir / "pointers").get("groundedness")
    assert active is None


@pytest.mark.asyncio
async def test_apply_promotion_gate_blocks_real_adapter_without_served_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FLYCHAIN_DATA_DIR", str(tmp_path / "flychain-data"))
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    run = TrainingRun(
        id="run_real_unvalidated",
        capability_id="groundedness",
        recipe_id="sft-mlx-lora",
        dataset_id="ds_demo",
        dataset_path=str(data_dir / "datasets" / "groundedness" / "demo.jsonl"),
        status="trained",
        created_at="2026-04-22T00:00:00+00:00",
        updated_at="2026-04-22T00:00:00+00:00",
        artifact={
            "backend": "mlx-lm",
            "adapter_dir": str(data_dir / "runs" / "run_real_unvalidated" / "artifacts"),
            "base_model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
            "dry_run": False,
        },
        baseline={"groundedness": 0.6},
        candidate={},
    )
    TrainingRunStore(data_dir / "runs").save(run)

    result = await apply_promotion_gate(
        {},
        run_id=run.id,
        candidate={"groundedness": 0.72},
        baseline=None,
    )

    assert result["status"] == "archived"
    assert result["gate_verdict"]["decision"] == "archive"
    assert "served validation" in result["gate_verdict"]["reason"]
    assert AdapterPointerStore(data_dir / "pointers").get("groundedness") is None


@pytest.mark.asyncio
async def test_apply_promotion_gate_promotes_validated_real_adapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FLYCHAIN_DATA_DIR", str(tmp_path / "flychain-data"))
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    run = TrainingRun(
        id="run_real_validated",
        capability_id="groundedness",
        recipe_id="sft-mlx-lora",
        dataset_id="ds_demo",
        dataset_path=str(data_dir / "datasets" / "groundedness" / "demo.jsonl"),
        status="validated",
        created_at="2026-04-22T00:00:00+00:00",
        updated_at="2026-04-22T00:00:00+00:00",
        artifact={
            "backend": "mlx-lm",
            "adapter_dir": str(data_dir / "runs" / "run_real_validated" / "artifacts"),
            "base_model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
            "dry_run": False,
        },
        baseline={"groundedness": 0.6},
        candidate={},
        served_validation={
            "status": "passed",
            "aggregate_score": 1.0,
            "validation_trace_ids": ["trace_validated"],
            "provider": "local-mlx",
            "model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
            "adapter_run_id": "run_real_validated",
            "adapter_capability_id": "groundedness",
            "routing_mode": "candidate",
            "failures": [],
        },
    )
    TrainingRunStore(data_dir / "runs").save(run)

    result = await apply_promotion_gate(
        {},
        run_id=run.id,
        candidate={"groundedness": 0.72},
        baseline=None,
    )

    assert result["status"] == "promoted"
    active = AdapterPointerStore(data_dir / "pointers").get("groundedness")
    assert active is not None
    assert active["active_run_id"] == run.id


@pytest.mark.asyncio
async def test_apply_promotion_gate_blocks_forged_served_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FLYCHAIN_DATA_DIR", str(tmp_path / "flychain-data"))
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    run = TrainingRun(
        id="run_real_forged",
        capability_id="groundedness",
        recipe_id="sft-mlx-lora",
        dataset_id="ds_demo",
        dataset_path=str(data_dir / "datasets" / "groundedness" / "demo.jsonl"),
        status="validated",
        created_at="2026-04-22T00:00:00+00:00",
        updated_at="2026-04-22T00:00:00+00:00",
        artifact={
            "backend": "mlx-lm",
            "adapter_dir": str(data_dir / "runs" / "run_real_forged" / "artifacts"),
            "base_model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
            "dry_run": False,
        },
        baseline={"groundedness": 0.6},
        candidate={},
        served_validation={"status": "passed", "aggregate_score": 1.0},
    )
    TrainingRunStore(data_dir / "runs").save(run)

    result = await apply_promotion_gate(
        {},
        run_id=run.id,
        candidate={"groundedness": 0.72},
        baseline=None,
    )

    assert result["status"] == "archived"
    assert "served validation" in result["gate_verdict"]["reason"]
    assert AdapterPointerStore(data_dir / "pointers").get("groundedness") is None
