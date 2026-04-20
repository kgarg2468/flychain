"""OpenTelemetry setup with OpenInference-style attributes.

We emit spans for every gateway proxy call. Attributes follow the
OpenInference naming convention (``llm.*``, ``input.value``, ``output.value``)
so existing observability tools (Langfuse, Arize, Datadog APM) can ingest
without a custom mapping.
"""

from __future__ import annotations

import json
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_TRACER_NAME = "flychain.gateway"


def setup_tracing(service_name: str = "flychain-gateway", otlp_endpoint: str | None = None) -> None:
    """Idempotently configure a global TracerProvider.

    When ``otlp_endpoint`` is not provided no exporter is attached - spans are
    still created (useful for attribute introspection in tests) but never
    emitted anywhere. This keeps the laptop dev loop quiet by default.
    """
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider) and getattr(provider, "_flychain_configured", False):
        return

    resource = Resource.create({"service.name": service_name})
    new_provider = TracerProvider(resource=resource)

    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            exporter: Any = OTLPSpanExporter(endpoint=otlp_endpoint)
            new_provider.add_span_processor(BatchSpanProcessor(exporter))
        except Exception:  # pragma: no cover
            pass

    new_provider._flychain_configured = True  # type: ignore[attr-defined]
    trace.set_tracer_provider(new_provider)


def get_tracer() -> trace.Tracer:
    return trace.get_tracer(_TRACER_NAME)


def make_llm_attributes(
    *,
    provider: str,
    model: str,
    method: str,
    request_payload: dict[str, Any],
    response_payload: dict[str, Any] | None,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    latency_ms: int,
    project_id: str,
) -> dict[str, Any]:
    """Build an OpenInference-compatible attribute bag for an LLM span."""

    # Keep payloads bounded to avoid unbounded exporter sizes.
    req_str = _truncate(json.dumps(request_payload, ensure_ascii=False), 16 * 1024)
    res_str = _truncate(
        json.dumps(response_payload or {}, ensure_ascii=False),
        16 * 1024,
    )

    return {
        "openinference.span.kind": "LLM",
        "llm.provider": provider,
        "llm.model_name": model,
        "llm.invocation_type": method,
        "llm.token_count.prompt": prompt_tokens,
        "llm.token_count.completion": completion_tokens,
        "llm.token_count.total": total_tokens,
        "llm.latency_ms": latency_ms,
        "input.value": req_str,
        "input.mime_type": "application/json",
        "output.value": res_str,
        "output.mime_type": "application/json",
        "flychain.project_id": project_id,
    }


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "...[truncated]"
