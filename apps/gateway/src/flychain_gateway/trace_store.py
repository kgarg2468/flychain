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
from collections.abc import Callable
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
        self._client_lock = threading.Lock()
        self._buffer = _Buffer()

    # -- lifecycle -------------------------------------------------------

    def connect(self) -> None:
        if self._client is not None or clickhouse_connect is None:
            return
        with self._client_lock:
            if self._client is not None:
                return
            parsed = urlparse(self.url)
            scheme = parsed.scheme or "http"
            host = parsed.hostname or "localhost"
            port = parsed.port or (8443 if scheme == "https" else 8123)
            user = parsed.username or "default"
            password = parsed.password or ""
            database = parsed.path.lstrip("/") or "flychain"
            try:
                client = clickhouse_connect.get_client(
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
                client.ping()
                self._ensure_eval_score_columns(client)
                self._ensure_feedback_columns(client)
                self._client = client
            except Exception as exc:  # pragma: no cover - depends on env
                logger.warning(
                    "ClickHouse unavailable (%s); falling back to in-memory buffer",
                    exc,
                )
                self._client = None

    def close(self) -> None:
        with self._client_lock:
            client = self._client
            self._client = None
            if client is not None:
                with contextlib.suppress(Exception):  # pragma: no cover
                    client.close()

    def _ensure_eval_score_columns(self, client: Client) -> None:
        try:
            columns = {row[0] for row in client.query("DESCRIBE TABLE eval_scores").result_rows}
            if "evaluator_type" not in columns:
                client.command(
                    "ALTER TABLE eval_scores "
                    "ADD COLUMN IF NOT EXISTS evaluator_type LowCardinality(String) "
                    "DEFAULT 'llm_judge' AFTER judge_model"
                )
            if "evaluator_source" not in columns:
                client.command(
                    "ALTER TABLE eval_scores "
                    "ADD COLUMN IF NOT EXISTS evaluator_source LowCardinality(String) "
                    "DEFAULT judge_model AFTER evaluator_type"
                )
        except Exception as exc:  # pragma: no cover - depends on ClickHouse state
            logger.warning("ClickHouse eval_scores migration check failed: %s", exc)

    def _ensure_feedback_columns(self, client: Client) -> None:
        try:
            columns = {row[0] for row in client.query("DESCRIBE TABLE feedback").result_rows}
            if "correction_source" not in columns:
                client.command(
                    "ALTER TABLE feedback "
                    "ADD COLUMN IF NOT EXISTS correction_source LowCardinality(String) "
                    "DEFAULT 'human' AFTER corrected_response"
                )
            if "correction_metadata" not in columns:
                client.command(
                    "ALTER TABLE feedback "
                    "ADD COLUMN IF NOT EXISTS correction_metadata String "
                    "DEFAULT '' AFTER correction_source"
                )
        except Exception as exc:  # pragma: no cover - depends on ClickHouse state
            logger.warning("ClickHouse feedback migration check failed: %s", exc)

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
                    "evaluator_type": r.get("evaluator_type", "llm_judge") or "llm_judge",
                    "evaluator_source": r.get("evaluator_source", "")
                    or r.get("judge_model", "")
                    or "",
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
        correction_source: str = "human",
        correction_metadata: dict[str, Any] | None = None,
    ) -> None:
        row = {
            "feedback_id": feedback_id,
            "trace_id": trace_id,
            "project_id": project_id,
            "score": score,
            "thumb": thumb,
            "comment": comment,
            "corrected_response": corrected_response,
            "correction_source": correction_source or "human",
            "correction_metadata": json.dumps(correction_metadata or {}),
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

    def list_eval_scores(
        self,
        *,
        project_id: str | None = None,
        capability_id: str | None = None,
        trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = self._merge_rows(
            persisted=self._query_rows(
                "SELECT trace_id, project_id, capability_id, dimension, score, passed, "
                "reason, judge_model, evaluator_type, evaluator_source, ts FROM eval_scores"
            ),
            buffered=self.buffered_eval_scores(),
            key=lambda row: (
                row["trace_id"],
                row["capability_id"],
                row["dimension"],
                row.get("evaluator_source") or row.get("judge_model", ""),
            ),
        )
        if project_id is not None:
            rows = [row for row in rows if row["project_id"] == project_id]
        if capability_id is not None:
            rows = [row for row in rows if row["capability_id"] == capability_id]
        if trace_id is not None:
            rows = [row for row in rows if row["trace_id"] == trace_id]
        rows.sort(key=lambda row: row.get("ts", ""), reverse=True)
        return rows

    def list_traces(
        self,
        *,
        project_id: str | None = None,
        capability_id: str | None = None,
        status: str | None = None,
        provider: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._normalize_traces(
            self._merge_rows(
                persisted=self._normalize_traces(
                    self._query_rows(
                        "SELECT trace_id, span_id, parent_span_id, project_id, capability_ids, "
                        "provider, model, method, request, response, prompt_tokens, "
                        "completion_tokens, total_tokens, cost_usd, latency_ms, status, error, "
                        "tags, ts FROM traces"
                    )
                ),
                buffered=self.buffered_traces(),
                key=lambda row: (row["trace_id"], row["project_id"], row["method"]),
            )
        )

        if project_id is not None:
            rows = [row for row in rows if row["project_id"] == project_id]
        if status is not None:
            rows = [row for row in rows if row["status"] == status]
        if provider is not None:
            rows = [row for row in rows if row["provider"] == provider]
        if capability_id is not None:
            trace_ids = {
                row["trace_id"]
                for row in self.list_eval_scores(capability_id=capability_id, project_id=project_id)
            }
            rows = [row for row in rows if row["trace_id"] in trace_ids]

        rows.sort(key=lambda row: row.get("ts", ""), reverse=True)
        total = len(rows)
        return rows[offset : offset + limit], total

    def list_feedback(self) -> list[dict[str, Any]]:
        rows = self._merge_rows(
            persisted=self._query_rows(
                "SELECT feedback_id, trace_id, project_id, score, thumb, comment, "
                "corrected_response, correction_source, correction_metadata, ts FROM feedback"
            ),
            buffered=self.buffered_feedback(),
            key=lambda row: row["feedback_id"],
        )
        for row in rows:
            row.setdefault("correction_source", "human")
            row.setdefault("correction_metadata", "")
        return rows

    # -- internals -------------------------------------------------------

    def _query_rows(self, sql: str) -> list[dict[str, Any]]:
        self.connect()
        client = self._client
        if client is None:
            return []
        try:
            with self._client_lock:
                result = client.query(sql)
        except Exception as exc:  # pragma: no cover - env dependent
            logger.warning("ClickHouse query failed: %s", exc)
            return []

        column_names = list(getattr(result, "column_names", []) or [])
        rows = list(getattr(result, "result_rows", []) or [])
        return [dict(zip(column_names, row, strict=False)) for row in rows]

    def _merge_rows(
        self,
        *,
        persisted: list[dict[str, Any]],
        buffered: list[dict[str, Any]],
        key: Callable[[dict[str, Any]], Any],
    ) -> list[dict[str, Any]]:
        merged: dict[Any, dict[str, Any]] = {}
        for row in persisted + buffered:
            normalized = _normalize_row(row)
            merged[key(normalized)] = normalized
        return list(merged.values())

    def _normalize_traces(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for row in rows:
            item = _normalize_row(row)
            for field_name in ("request", "response"):
                value = item.get(field_name)
                if isinstance(value, str):
                    with contextlib.suppress(json.JSONDecodeError):
                        item[field_name] = json.loads(value)
            tags = item.get("tags")
            if not isinstance(tags, dict):
                item["tags"] = dict(tags or {})
            capabilities = item.get("capability_ids")
            if capabilities is None:
                item["capability_ids"] = []
            elif not isinstance(capabilities, list):
                item["capability_ids"] = list(capabilities)
            normalized.append(item)
        return normalized

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
            with self._client_lock:
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
            with self._client_lock:
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
                            r["evaluator_type"],
                            r["evaluator_source"],
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
                        "evaluator_type",
                        "evaluator_source",
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
            with self._client_lock:
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
                            r.get("correction_source", "human"),
                            r.get("correction_metadata", ""),
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
                        "correction_source",
                        "correction_metadata",
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
        "capability_ids": list(trace.capability_ids),
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


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    ts = normalized.get("ts")
    if isinstance(ts, datetime):
        normalized["ts"] = ts.astimezone(UTC).isoformat()
    if "passed" in normalized:
        normalized["passed"] = bool(normalized["passed"])
    if "evaluator_type" not in normalized:
        normalized["evaluator_type"] = "llm_judge"
    if "evaluator_source" not in normalized:
        normalized["evaluator_source"] = normalized.get("judge_model", "")
    return normalized
