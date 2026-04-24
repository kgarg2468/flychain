"""arq worker entrypoint for FlyChain's background jobs."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from arq.connections import RedisSettings
from flychain_capability_compiler import apply_gate, recipe_by_id, select_backend
from flychain_gateway.capability_store import default_data_dir
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


async def evaluate_trace(
    ctx: dict,
    *,
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
    return await post_eval(
        gateway_url=gateway_url,
        trace_id=trace_id,
        project_id=project_id,
        input_text=input_text,
        output_text=output_text,
        context=context,
        tags=tags,
        capability_ids=capability_ids,
    )


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


async def run_training_recipe(ctx: dict, *, run_id: str) -> dict[str, Any]:
    """Execute a queued training run and persist its artifact."""
    run_store, run, data_root = _load_run(run_id)
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
        raise

    run.artifact = artifact.as_dict()
    run.status = "trained"
    run.error = None
    run.updated_at = _now_iso()
    run_store.save(run)
    return asdict(run)


async def apply_promotion_gate(
    ctx: dict,
    *,
    run_id: str,
    candidate: dict[str, float],
    baseline: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Apply the promotion gate and update the active adapter pointer if promoted."""
    run_store, run, data_root = _load_run(run_id)
    adapter_store = AdapterPointerStore(data_root / "pointers")

    run.status = "gate-running"
    run.error = None
    run.updated_at = _now_iso()
    run_store.save(run)

    try:
        recipe = recipe_by_id(run.recipe_id)
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
        raise

    run.baseline = effective_baseline
    run.candidate = dict(candidate)
    run.gate_verdict = verdict.as_dict()
    run.status = "promoted" if verdict.promoted() else "archived"
    run.error = None
    run.updated_at = _now_iso()
    run_store.save(run)

    if verdict.promoted() and run.artifact is not None:
        adapter_store.set_active(
            run.capability_id,
            run_id=run.id,
            adapter_dir=str(run.artifact.get("adapter_dir", "")),
            baseline=run.baseline,
            candidate=run.candidate,
        )

    return asdict(run)


def _redis_settings_from_url(url: str) -> RedisSettings:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int((parsed.path or "/0").lstrip("/") or 0),
    )


class WorkerSettings:
    functions = [noop, evaluate_trace, run_training_recipe, apply_promotion_gate]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _redis_settings_from_url(get_settings().redis_url)
