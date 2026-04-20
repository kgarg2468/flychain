"""Smoke tests for the orchestrator worker."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from flychain_orchestrator import eval_client
from flychain_orchestrator.worker import WorkerSettings, evaluate_trace, noop


@pytest.mark.asyncio
async def test_noop_returns_ok() -> None:
    result = await noop({})
    assert result == "ok"


def test_worker_settings_has_functions() -> None:
    assert noop in WorkerSettings.functions
    assert evaluate_trace in WorkerSettings.functions


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
