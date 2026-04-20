"""arq worker entrypoint.

Phase 0 scaffold turned on in Phase 4: registers concrete tasks so the
gateway (or a poller) can enqueue background eval jobs.

Registered tasks:

    * ``evaluate_trace`` - run the auto-eval pipeline for a trace by calling
      the gateway's ``/v1/eval`` endpoint.

Phase 5 will add ``cluster_failures`` + ``synthesize_dataset``; Phase 6 will
add ``run_training_recipe`` + ``apply_promotion_gate``.
"""

from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings

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


def _redis_settings_from_url(url: str) -> RedisSettings:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int((parsed.path or "/0").lstrip("/") or 0),
    )


class WorkerSettings:
    functions = [noop, evaluate_trace]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _redis_settings_from_url(get_settings().redis_url)
