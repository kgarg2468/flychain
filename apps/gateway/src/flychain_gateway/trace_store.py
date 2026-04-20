"""ClickHouse trace store.

Writes trace records and feedback rows to ClickHouse. Falls back to an
in-memory buffer when ClickHouse is unavailable so tests and laptop-first
dev modes keep working without the container stack up.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

try:
    import clickhouse_connect
    from clickhouse_connect.driver.client import Client
except Exception:  # pragma: no cover - optional import
    clickhouse_connect = None  # type: ignore[assignment]
    Client = Any  # type: ignore[assignment,misc]

from flychain_gateway.schemas import TraceRecord

logger = logging.getLogger(__name__)


@dataclass
class _Buffer:
    """Tiny in-memory buffer used when ClickHouse is unavailable."""

    traces: list[dict[str, Any]] = field(default_factory=list)
    eval_scores: list[dict[str, Any]] = field(default_factory=list)
    failure_embeddings: list[dict[str, Any]] = field(default_factory=list)
    feedback: list[dict[str, Any]] = field(default_factory=list)


class TraceStore:
    """Asynchronous wrapper around a ClickHouse client."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._client: Any | None = None
        self._lock = threading.Lock()
        self._buffer = _Buffer()

    # -- lifecycle -------------------------------------------------------

    def connect(self) -> None:
        if self._client is not None or clickhouse_connect is None:
            return
        parsed = urlparse(self.url)
        scheme = parsed.scheme or "http"
        host = parsed.hostname or "localhost"
        port = parsed.port or (8443 if scheme == "https" else 8123)
        user = parsed.username or "default"
        password = parsed.password or ""
        database = parsed.path.lstrip("/") or "flychain"
        try:
            self._client = clickhouse_connect.get_client(
                host=host,
                port=port,
                username=user,
                password=password,
                database=database,
                interface=scheme,
                compress=False,
                connect_timeout=2,
                query_limit=0,
            )
            self._client.ping()
        except Exception as exc:  # pragma: no cover - depends on env
            logger.warning("ClickHouse unavailable (%s); falling back to in-memory buffer", exc)
            self._client = None

    def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            with contextlib.suppress(Exception):  # pragma: no cover
                client.close()

    # -- writes ----------------------------------------------------------

    async def insert_trace(self, trace: TraceRecord) -> None:
        row = _trace_to_row(trace)
        with self._lock:
            self._buffer.traces.append(row)
        await asyncio.to_thread(self._flush_traces)

    async def insert_eval_scores(self, rows: list[dict[str, Any]]) -> None:
        """Persist per-trace, per-capability, per-dimension eval scores."""
        if not rows:
            return
        enriched = []
        for r in rows:
            enriched.append(
                {
                    "trace_id": r["trace_id"],
                    "project_id": r["project_id"],
                    "capability_id": r["capability_id"],
                    "dimension": r["dimension"],
                    "score": float(r["score"]),
                    "passed": 1 if r.get("passed") else 0,
                    "reason": r.get("reason", "") or "",
                    "judge_model": r.get("judge_model", "") or "",
                    "ts": datetime.now(UTC),
                }
            )
        with self._lock:
            self._buffer.eval_scores.extend(enriched)
        await asyncio.to_thread(self._flush_eval_scores)

    async def insert_feedback(
        self,
        feedback_id: str,
        trace_id: str,
        project_id: str,
        thumb: str,
        score: int,
        comment: str,
        corrected_response: str,
    ) -> None:
        row = {
            "feedback_id": feedback_id,
            "trace_id": trace_id,
            "project_id": project_id,
            "score": score,
            "thumb": thumb,
            "comment": comment,
            "corrected_response": corrected_response,
            "ts": datetime.now(UTC),
        }
        with self._lock:
            self._buffer.feedback.append(row)
        await asyncio.to_thread(self._flush_feedback)

    # -- reads (diagnostic) ---------------------------------------------

    def buffered_traces(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._buffer.traces)

    def buffered_feedback(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._buffer.feedback)

    def buffered_eval_scores(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._buffer.eval_scores)

    # -- internals -------------------------------------------------------

    def _flush_traces(self) -> None:
        self.connect()
        client = self._client
        if client is None:
            return
        with self._lock:
            rows = self._buffer.traces
            self._buffer.traces = []
        if not rows:
            return
        try:
            client.insert(
                "traces",
                [
                    [
                        r["trace_id"],
                        r["span_id"],
                        r["parent_span_id"],
                        r["project_id"],
                        r["capability_ids"],
                        r["provider"],
                        r["model"],
                        r["method"],
                        r["request"],
                        r["response"],
                        r["prompt_tokens"],
                        r["completion_tokens"],
                        r["total_tokens"],
                        r["cost_usd"],
                        r["latency_ms"],
                        r["status"],
                        r["error"],
                        r["tags"],
                        r["ts"],
                    ]
                    for r in rows
                ],
                column_names=[
                    "trace_id",
                    "span_id",
                    "parent_span_id",
                    "project_id",
                    "capability_ids",
                    "provider",
                    "model",
                    "method",
                    "request",
                    "response",
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "cost_usd",
                    "latency_ms",
                    "status",
                    "error",
                    "tags",
                    "ts",
                ],
            )
        except Exception as exc:
            logger.warning("ClickHouse trace insert failed: %s", exc)
            with self._lock:
                self._buffer.traces = rows + self._buffer.traces

    def _flush_eval_scores(self) -> None:
        self.connect()
        client = self._client
        if client is None:
            return
        with self._lock:
            rows = self._buffer.eval_scores
            self._buffer.eval_scores = []
        if not rows:
            return
        try:
            client.insert(
                "eval_scores",
                [
                    [
                        r["trace_id"],
                        r["project_id"],
                        r["capability_id"],
                        r["dimension"],
                        r["score"],
                        r["passed"],
                        r["reason"],
                        r["judge_model"],
                        r["ts"],
                    ]
                    for r in rows
                ],
                column_names=[
                    "trace_id",
                    "project_id",
                    "capability_id",
                    "dimension",
                    "score",
                    "passed",
                    "reason",
                    "judge_model",
                    "ts",
                ],
            )
        except Exception as exc:
            logger.warning("ClickHouse eval_scores insert failed: %s", exc)
            with self._lock:
                self._buffer.eval_scores = rows + self._buffer.eval_scores

    def _flush_feedback(self) -> None:
        self.connect()
        client = self._client
        if client is None:
            return
        with self._lock:
            rows = self._buffer.feedback
            self._buffer.feedback = []
        if not rows:
            return
        try:
            client.insert(
                "feedback",
                [
                    [
                        r["feedback_id"],
                        r["trace_id"],
                        r["project_id"],
                        r["score"],
                        r["thumb"],
                        r["comment"],
                        r["corrected_response"],
                        r["ts"],
                    ]
                    for r in rows
                ],
                column_names=[
                    "feedback_id",
                    "trace_id",
                    "project_id",
                    "score",
                    "thumb",
                    "comment",
                    "corrected_response",
                    "ts",
                ],
            )
        except Exception as exc:
            logger.warning("ClickHouse feedback insert failed: %s", exc)
            with self._lock:
                self._buffer.feedback = rows + self._buffer.feedback


def _trace_to_row(trace: TraceRecord) -> dict[str, Any]:
    return {
        "trace_id": trace.trace_id,
        "span_id": "",
        "parent_span_id": "",
        "project_id": trace.project_id,
        "capability_ids": [],
        "provider": trace.provider,
        "model": trace.model,
        "method": trace.method,
        "request": json.dumps(trace.request, ensure_ascii=False),
        "response": json.dumps(trace.response or {}, ensure_ascii=False),
        "prompt_tokens": trace.prompt_tokens,
        "completion_tokens": trace.completion_tokens,
        "total_tokens": trace.total_tokens,
        "cost_usd": float(trace.cost_usd),
        "latency_ms": trace.latency_ms,
        "status": trace.status,
        "error": trace.error,
        "tags": dict(trace.tags),
        "ts": datetime.now(UTC),
    }
