"""arq worker entrypoint for FlyChain's background jobs."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
from arq.connections import RedisSettings
from flychain_capability_compiler import apply_gate, recipe_by_id, select_backend
from flychain_gateway.capability_store import default_data_dir
from flychain_gateway.job_store import JobStore
from flychain_gateway.served_validation import served_validation_errors
from flychain_gateway.training_store import AdapterPointerStore, TrainingRun, TrainingRunStore

from flychain_orchestrator.config import get_settings
from flychain_orchestrator.eval_client import post_eval


async def startup(ctx: dict) -> None:
    ctx["settings"] = get_settings()


async def shutdown(ctx: dict) -> None:
    ctx.clear()


async def noop(ctx: dict) -> str:
    """Placeholder task kept for health-checks."""
    return "ok"


async def trigger_autopilot(ctx: dict, *, capability_id: str, trigger: str) -> None:
    settings = ctx.get("settings") or get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        resp = await client.post(
            f"{settings.gateway_url.rstrip('/')}/v1/capabilities/{capability_id}/autopilot/run",
            json={"trigger": trigger},
        )
        resp.raise_for_status()


async def _with_job_timeout(job_store: JobStore, job_id: str | None, awaitable):
    timeout_seconds: int | None = None
    if job_id:
        job = job_store.load(job_id)
        timeout_seconds = job.timeout_seconds if job is not None else None
    try:
        if timeout_seconds is None:
            return await awaitable
        return await asyncio.wait_for(awaitable, timeout=timeout_seconds)
    except TimeoutError:
        if job_id:
            job_store.timeout(job_id, error=f"job timed out after {timeout_seconds}s")
        raise


async def evaluate_trace(
    ctx: dict,
    *,
    job_id: str | None = None,
    trace_id: str,
    project_id: str,
    input_text: str,
    output_text: str,
    context: str = "",
    tags: dict[str, str] | None = None,
    capability_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Run auto-eval for a trace by calling the gateway."""
    settings = ctx.get("settings") or get_settings()
    gateway_url = getattr(settings, "gateway_url", None) or None
    job_store = JobStore(default_data_dir() / "jobs")
    if job_id:
        job_store.start(job_id)
    try:
        result = await _with_job_timeout(
            job_store,
            job_id,
            post_eval(
                gateway_url=gateway_url,
                trace_id=trace_id,
                project_id=project_id,
                input_text=input_text,
                output_text=output_text,
                context=context,
                tags=tags,
                capability_ids=capability_ids,
            ),
        )
    except TimeoutError:
        raise
    except Exception as exc:
        if job_id:
            job_store.fail(job_id, error=str(exc))
        raise
    if job_id:
        job_store.succeed(job_id)
    return result


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _load_run(run_id: str) -> tuple[TrainingRunStore, TrainingRun, Path]:
    data_root = default_data_dir()
    run_store = TrainingRunStore(data_root / "runs")
    run = run_store.load(run_id)
    if run is None:
        raise ValueError(f"no such training run: {run_id}")
    return run_store, run, data_root


async def run_training_recipe(ctx: dict, *, run_id: str, job_id: str | None = None) -> dict[str, Any]:
    """Execute a queued training run and persist its artifact."""
    run_store, run, data_root = _load_run(run_id)
    job_store = JobStore(data_root / "jobs")
    if job_id:
        job_store.start(job_id)
    run.status = "running"
    run.error = None
    run.updated_at = _now_iso()
    run_store.save(run)

    try:
        recipe = recipe_by_id(run.recipe_id)
        dataset_path = Path(run.dataset_path)
        if not dataset_path.exists():
            raise FileNotFoundError(f"dataset path does not exist: {dataset_path}")
        backend = select_backend(
            recipe.backend.value,
            allow_fallback=run.allow_backend_fallback,
        )
        artifact = backend.run(
            recipe=recipe,
            dataset_path=dataset_path,
            output_dir=data_root / "runs" / run_id / "artifacts",
        )
    except Exception as exc:
        run.status = "failed"
        run.error = str(exc)
        run.updated_at = _now_iso()
        run_store.save(run)
        if job_id:
            job_store.fail(job_id, error=str(exc))
        raise

    run.artifact = artifact.as_dict()
    run.status = "trained"
    run.error = None
    run.updated_at = _now_iso()
    run_store.save(run)
    if job_id:
        job_store.succeed(job_id)
    with suppress(Exception):
        await trigger_autopilot(ctx, capability_id=run.capability_id, trigger="training_completed")
    return asdict(run)


def _requires_served_validation(run: TrainingRun) -> bool:
    artifact = run.artifact or {}
    return artifact.get("backend") == "mlx-lm" and not bool(artifact.get("dry_run"))


def _has_passed_served_validation(run: TrainingRun) -> bool:
    if not _requires_served_validation(run):
        return True
    return not served_validation_errors(run)


def _archive_for_missing_validation(
    run: TrainingRun, run_store: TrainingRunStore, errors: list[str] | None = None
) -> dict[str, Any]:
    reason = "served validation is required before promotion"
    if errors:
        reason = "served validation proof is incomplete: " + "; ".join(errors)
    run.gate_verdict = {
        "decision": "archive",
        "target_capability_id": run.capability_id,
        "target_delta": 0.0,
        "threshold": 0.0,
        "max_other_regression": 0.0,
        "deltas": [],
        "regressions": [],
        "reason": reason,
    }
    run.status = "archived"
    run.error = reason
    run.updated_at = _now_iso()
    run_store.save(run)
    return asdict(run)


async def apply_promotion_gate(
    ctx: dict,
    *,
    run_id: str,
    candidate: dict[str, float],
    baseline: dict[str, float] | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Apply the promotion gate and update the active adapter pointer if promoted."""
    run_store, run, data_root = _load_run(run_id)
    adapter_store = AdapterPointerStore(data_root / "pointers")
    job_store = JobStore(data_root / "jobs")
    if job_id:
        job_store.start(job_id)

    run.status = "gate-running"
    run.error = None
    run.updated_at = _now_iso()
    run_store.save(run)

    try:
        recipe = recipe_by_id(run.recipe_id)
        validation_errors = served_validation_errors(run)
        if validation_errors:
            result = _archive_for_missing_validation(run, run_store, validation_errors)
            if job_id:
                job_store.succeed(job_id)
            return result
        effective_baseline = dict(baseline if baseline is not None else run.baseline)
        verdict = apply_gate(
            target_capability_id=run.capability_id,
            baseline=effective_baseline,
            candidate=candidate,
            threshold=recipe.promotion_threshold,
            max_other_regression=recipe.max_other_regression,
        )
    except Exception as exc:
        run.status = "failed"
        run.error = str(exc)
        run.updated_at = _now_iso()
        run_store.save(run)
        if job_id:
            job_store.fail(job_id, error=str(exc))
        raise

    run.baseline = effective_baseline
    run.candidate = dict(candidate)
    run.gate_verdict = verdict.as_dict()
    run.status = "promoted" if verdict.promoted() else "archived"
    run.error = None
    run.updated_at = _now_iso()
    run_store.save(run)

    if (
        verdict.promoted()
        and run.artifact is not None
        and not bool(run.artifact.get("dry_run"))
        and _has_passed_served_validation(run)
    ):
        adapter_store.set_active(
            run.capability_id,
            run_id=run.id,
            adapter_dir=str(run.artifact.get("adapter_dir", "")),
            baseline=run.baseline,
            candidate=run.candidate,
        )

    if job_id:
        job_store.succeed(job_id)
    return asdict(run)


async def run_served_validation(
    ctx: dict,
    *,
    run_id: str,
    replay_set_id: str,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Ask the gateway to run served adapter validation for a queued run."""
    settings = ctx.get("settings") or get_settings()
    payload = {"replay_set_id": replay_set_id, "job_id": job_id}
    job_store = JobStore(default_data_dir() / "jobs")
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0)) as client:
        resp = await _with_job_timeout(
            job_store,
            job_id,
            client.post(
                f"{settings.gateway_url.rstrip('/')}/internal/training-runs/{run_id}/served-validation/run",
                json=payload,
            ),
        )
        resp.raise_for_status()
        result = resp.json()
    with suppress(Exception):
        _run_store, run, _data_root = _load_run(run_id)
        await trigger_autopilot(
            ctx,
            capability_id=run.capability_id,
            trigger="served_validation_completed",
        )
    return result


def _redis_settings_from_url(url: str) -> RedisSettings:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int((parsed.path or "/0").lstrip("/") or 0),
    )


class WorkerSettings:
    functions = [
        noop,
        evaluate_trace,
        run_training_recipe,
        apply_promotion_gate,
        run_served_validation,
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _redis_settings_from_url(get_settings().redis_url)
