"""Orchestrator eval adapter.

Provides a thin wrapper around the FlyChain gateway's ``/v1/eval`` endpoint
so async arq tasks can trigger evaluations without re-implementing the
judge pipeline.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


async def post_eval(
    *,
    gateway_url: str | None = None,
    trace_id: str,
    project_id: str,
    input_text: str,
    output_text: str,
    context: str = "",
    tags: dict[str, str] | None = None,
    capability_ids: list[str] | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    url = (gateway_url or os.environ.get("FLYCHAIN_GATEWAY_URL", "http://localhost:8080")).rstrip(
        "/"
    )
    payload: dict[str, Any] = {
        "trace_id": trace_id,
        "project_id": project_id,
        "input": input_text,
        "output": output_text,
        "context": context,
        "tags": tags or {},
    }
    if capability_ids:
        payload["capability_ids"] = capability_ids

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0)) as client:
        resp = await client.post(f"{url}/v1/eval", json=payload)
        resp.raise_for_status()
        return resp.json()
