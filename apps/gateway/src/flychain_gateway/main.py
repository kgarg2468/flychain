"""FlyChain gateway FastAPI application."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from flychain_capability_compiler import (
    CapabilityCompiler,
    CapabilitySpec,
    Cluster,
    EvalEngine,
    FailedTrace,
    SynthesizedDataset,
    TraceData,
    aggregate_score,
    auto_client,
    auto_embedder,
    cluster_failures,
    list_recipes,
    list_templates,
    recipe_by_id,
    synthesize_dpo_dataset,
    synthesize_sft_dataset,
    template_by_id,
    write_jsonl,
)
from opentelemetry import trace as otel_trace
from pydantic import BaseModel, ConfigDict
from ulid import ULID

from flychain_gateway import __version__
from flychain_gateway.autopilot_store import AutopilotPolicy, AutopilotStore
from flychain_gateway.capability_store import (
    CapabilityExistsError,
    CapabilityNotFoundError,
    CapabilityStore,
    default_data_dir,
    slugify,
)
from flychain_gateway.cluster_store import ClusterStore, DatasetStore
from flychain_gateway.config import Settings, get_settings
from flychain_gateway.failure_review_store import FailureReviewStore
from flychain_gateway.job_store import JobRecord, JobStore
from flychain_gateway.models_registry import ModelNotFoundError, ModelRegistry, get_registry
from flychain_gateway.otel import get_tracer, make_llm_attributes, setup_tracing
from flychain_gateway.providers.registry import ProviderRouter
from flychain_gateway.replay_store import ReplaySet, ReplaySetStore
from flychain_gateway.schemas import (
    AnthropicMessagesRequest,
    ChatCompletionRequest,
    ChatMessage,
    FeedbackAccepted,
    FeedbackRequest,
    TraceRecord,
)
from flychain_gateway.served_validation import served_validation_errors
from flychain_gateway.settings_store import LocalSettings, SettingsStore
from flychain_gateway.trace_store import TraceStore
from flychain_gateway.training_store import (
    AdapterPointerStore,
    TrainingRun,
    TrainingRunStore,
)

try:
    from arq.connections import ArqRedis, RedisSettings, create_pool
except Exception:  # pragma: no cover - optional import in some envs
    ArqRedis = Any  # type: ignore[assignment,misc]
    RedisSettings = Any  # type: ignore[assignment,misc]
    create_pool = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{ULID()}"


def _extract_headers(
    *,
    x_flychain_project: str | None,
    x_flychain_capabilities: str | None,
    x_flychain_tags: str | None,
    default_project_id: str,
) -> tuple[str, list[str], dict[str, str]]:
    project_id = x_flychain_project or default_project_id
    capabilities = (
        [c.strip() for c in x_flychain_capabilities.split(",") if c.strip()]
        if x_flychain_capabilities
        else []
    )
    tags: dict[str, str] = {}
    if x_flychain_tags:
        for pair in x_flychain_tags.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                tags[k.strip()] = v.strip()
    return project_id, capabilities, tags


def _redis_settings_from_url(url: str) -> RedisSettings:
    parsed = urlparse(url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int((parsed.path or "/0").lstrip("/") or 0),
    )


def _component_health(
    name: str,
    status: str,
    *,
    target: str | None = None,
    detail: str | None = None,
) -> dict[str, str]:
    row = {"name": name, "status": status}
    if target:
        row["target"] = target
    if detail:
        row["detail"] = detail
    return row


def _tcp_health(name: str, url: str, *, default_port: int) -> dict[str, str]:
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or default_port
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return _component_health(name, "ok", target=url)
    except OSError as exc:
        return _component_health(name, "down", target=url, detail=str(exc))


def _http_health(name: str, base_url: str | None, path: str) -> dict[str, str]:
    if not base_url:
        return _component_health(name, "not_configured")
    target = base_url.rstrip("/")
    try:
        resp = httpx.get(f"{target}{path}", timeout=0.8)
    except httpx.HTTPError as exc:
        return _component_health(name, "down", target=target, detail=str(exc))
    status = "ok" if resp.status_code < 500 else "degraded"
    return _component_health(name, status, target=target, detail=f"http {resp.status_code}")


def _clickhouse_health(store: TraceStore) -> dict[str, str]:
    client = getattr(store, "_client", None)
    if client is None:
        return _component_health(
            "ClickHouse",
            "degraded",
            target=store.url,
            detail="using in-memory trace buffer",
        )
    try:
        client.ping()
    except Exception as exc:  # pragma: no cover - depends on local service state
        return _component_health("ClickHouse", "down", target=store.url, detail=str(exc))
    return _component_health("ClickHouse", "ok", target=store.url)


def _runtime_health(app: FastAPI, settings: Settings) -> list[dict[str, str]]:
    redis = _tcp_health("Redis", settings.redis_url, default_port=6379)
    job_queue = getattr(app.state, "job_queue", None)
    if job_queue is None:
        jobs = _component_health("Background jobs", "disabled", detail="Redis queue unavailable")
    else:
        jobs = _component_health("Background jobs", "ok", target=settings.redis_url)

    return [
        _component_health("Gateway", "ok"),
        jobs,
        _clickhouse_health(app.state.trace_store),
        redis,
        _tcp_health("Postgres", settings.postgres_url, default_port=5432),
        _http_health("Ollama", settings.ollama_url, "/v1/models"),
        _http_health("MLX server", settings.mlx_server_url, "/v1/models"),
    ]


def _flatten_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    parts.append(text)
                continue
            if not isinstance(item, dict):
                continue
            text_value = item.get("text")
            if isinstance(text_value, str) and text_value.strip():
                parts.append(text_value.strip())
                continue
            content_value = item.get("content")
            if isinstance(content_value, str) and content_value.strip():
                parts.append(content_value.strip())
        return "\n".join(parts)
    return str(content).strip()


def _chat_input_text(messages: list[Any]) -> str:
    def _role(message: Any) -> str:
        if isinstance(message, dict):
            return str(message.get("role", ""))
        return str(getattr(message, "role", ""))

    def _content(message: Any) -> Any:
        if isinstance(message, dict):
            return message.get("content")
        return getattr(message, "content", None)

    parts = [
        _flatten_content(_content(message)) for message in messages if _role(message) == "user"
    ]
    joined = "\n".join(part for part in parts if part)
    if joined:
        return joined
    fallback = [_flatten_content(_content(message)) for message in messages]
    return "\n".join(part for part in fallback if part)


def _chat_output_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            return _flatten_content(message.get("content"))
    return ""


def _messages_output_text(payload: dict[str, Any]) -> str:
    return _flatten_content(payload.get("content"))


def _payload_dict(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        import json as _json

        try:
            parsed = _json.loads(payload)
        except _json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings.cache_clear()
    settings: Settings = get_settings()
    if settings.data_dir:
        os.environ["FLYCHAIN_DATA_DIR"] = settings.data_dir
    if settings.models_yaml:
        os.environ["FLYCHAIN_MODELS_YAML"] = settings.models_yaml
    if settings.templates_dir:
        os.environ["FLYCHAIN_TEMPLATES_DIR"] = settings.templates_dir
    if settings.recipes_dir:
        os.environ["FLYCHAIN_RECIPES_DIR"] = settings.recipes_dir
    os.environ.setdefault("FLYCHAIN_EMBEDDING_MODEL", settings.embedding_model)
    setup_tracing(service_name="flychain-gateway", otlp_endpoint=settings.otlp_endpoint)
    get_registry.cache_clear()
    registry: ModelRegistry = get_registry()
    store = TraceStore(settings.clickhouse_url)
    store.connect()
    router = ProviderRouter(settings=settings, registry=registry)

    data_root = default_data_dir()
    capability_store = CapabilityStore(data_root / "capabilities")
    cluster_store = ClusterStore(data_root / "clusters")
    dataset_store = DatasetStore(data_root / "datasets")
    training_run_store = TrainingRunStore(data_root / "runs")
    adapter_pointer_store = AdapterPointerStore(data_root / "pointers")
    autopilot_store = AutopilotStore(data_root / "autopilot")
    replay_set_store = ReplaySetStore(data_root / "replay-sets")
    failure_review_store = FailureReviewStore(data_root / "failure-reviews")
    local_settings_store = SettingsStore(data_root / "settings.json")
    job_store = JobStore(data_root / "jobs")
    job_queue: ArqRedis | None = None
    if create_pool is not None:
        try:
            job_queue = await create_pool(_redis_settings_from_url(settings.redis_url), retry=0)
        except Exception as exc:  # pragma: no cover - depends on env
            logger.warning("Redis queue unavailable (%s); background jobs disabled", exc)

    app.state.settings = settings
    app.state.registry = registry
    app.state.trace_store = store
    app.state.router = router
    app.state.capability_store = capability_store
    app.state.cluster_store = cluster_store
    app.state.dataset_store = dataset_store
    app.state.training_run_store = training_run_store
    app.state.adapter_pointer_store = adapter_pointer_store
    app.state.autopilot_store = autopilot_store
    app.state.replay_set_store = replay_set_store
    app.state.failure_review_store = failure_review_store
    app.state.local_settings_store = local_settings_store
    app.state.job_store = job_store
    app.state.job_queue = job_queue

    try:
        yield
    finally:
        if job_queue is not None:
            with suppress(Exception):
                await job_queue.aclose()
        store.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="FlyChain Gateway",
        version=__version__,
        lifespan=lifespan,
        description=(
            "OpenAI- and Anthropic-compatible proxy that records traces for the "
            "FlyChain capability-improvement flywheel."
        ),
    )

    def local_settings() -> LocalSettings:
        store: SettingsStore = app.state.local_settings_store
        return store.load()

    def judge_client(runtime: LocalSettings | None = None):
        settings = runtime or local_settings()
        return auto_client(
            prefer=settings.judge_provider,
            ollama_model=settings.judge_model,
        )

    def require_job_queue() -> ArqRedis:
        queue: ArqRedis | None = getattr(app.state, "job_queue", None)
        if queue is None:
            raise HTTPException(
                status_code=503,
                detail="background job queue is unavailable; check Redis/orchestrator",
            )
        return queue

    def create_job(
        *,
        job_type: str,
        capability_id: str | None = None,
        trace_ids: list[str] | None = None,
        cluster_id: str | None = None,
        dataset_id: str | None = None,
        run_id: str | None = None,
        replay_set_id: str | None = None,
        max_retries: int = 0,
        timeout_seconds: int | None = None,
        retry_payload: dict[str, Any] | None = None,
    ) -> JobRecord:
        job_store: JobStore = app.state.job_store
        return job_store.create(
            job_type=job_type,
            capability_id=capability_id,
            trace_ids=trace_ids,
            cluster_id=cluster_id,
            dataset_id=dataset_id,
            run_id=run_id,
            replay_set_id=replay_set_id,
            max_retries=max_retries,
            timeout_seconds=timeout_seconds,
            retry_payload=retry_payload,
        )

    async def wait_for_job_timeout(job: JobRecord, awaitable):
        if job.timeout_seconds is None:
            return await awaitable
        return await asyncio.wait_for(awaitable, timeout=job.timeout_seconds)

    async def maybe_enqueue_auto_eval(
        *,
        trace_id: str,
        project_id: str,
        input_text: str,
        output_text: str,
        tags: dict[str, str],
        capability_ids: list[str],
    ) -> None:
        runtime = local_settings()
        if not runtime.auto_eval_new_traces:
            return
        queue: ArqRedis | None = getattr(app.state, "job_queue", None)
        if queue is None:
            logger.warning(
                "auto-eval skipped for %s because background queue is unavailable", trace_id
            )
            return
        if not output_text.strip():
            return
        job = create_job(
            job_type="auto_eval",
            capability_id=capability_ids[0] if len(capability_ids) == 1 else None,
            trace_ids=[trace_id],
            max_retries=2,
            retry_payload={
                "function": "evaluate_trace",
                "kwargs": {
                    "trace_id": trace_id,
                    "project_id": project_id,
                    "input_text": input_text,
                    "output_text": output_text,
                    "context": "",
                    "tags": tags,
                    "capability_ids": capability_ids or None,
                },
            },
        )
        await queue.enqueue_job(
            "evaluate_trace",
            job_id=job.id,
            trace_id=trace_id,
            project_id=project_id,
            input_text=input_text,
            output_text=output_text,
            context="",
            tags=tags,
            capability_ids=capability_ids or None,
        )

    def list_all_traces(*, capability_id: str | None = None) -> list[dict[str, Any]]:
        store: TraceStore = app.state.trace_store
        offset = 0
        limit = 200
        collected: list[dict[str, Any]] = []
        while True:
            batch, total = store.list_traces(
                capability_id=capability_id,
                limit=limit,
                offset=offset,
            )
            collected.extend(batch)
            offset += len(batch)
            if not batch or offset >= total:
                break
        return collected

    def cluster_ids_by_trace(capability_id: str) -> dict[str, list[str]]:
        cluster_store: ClusterStore = app.state.cluster_store
        stored = cluster_store.load(capability_id) or {}
        mapping: dict[str, list[str]] = {}
        for cluster in stored.get("clusters", []):
            cluster_id = str(cluster.get("id", ""))
            for trace_id in cluster.get("trace_ids", []) or []:
                mapping.setdefault(str(trace_id), []).append(cluster_id)
        return mapping

    def latest_feedback_by_trace() -> dict[str, dict[str, Any]]:
        trace_store: TraceStore = app.state.trace_store
        feedback_rows = sorted(
            trace_store.list_feedback(),
            key=lambda row: row.get("ts", ""),
            reverse=True,
        )
        feedback_by_trace: dict[str, dict[str, Any]] = {}
        for row in feedback_rows:
            feedback_by_trace.setdefault(row["trace_id"], row)
        return feedback_by_trace

    def review_status_by_trace(capability_id: str) -> dict[str, str]:
        review_store: FailureReviewStore = app.state.failure_review_store
        return {
            review.trace_id: review.status
            for review in review_store.list_for_capability(capability_id)
        }

    def eval_score_summary(
        *,
        trace_id: str,
        capability_id: str | None = None,
    ) -> dict[str, Any]:
        trace_store: TraceStore = app.state.trace_store
        scores = trace_store.list_eval_scores(trace_id=trace_id, capability_id=capability_id)
        traces = [row for row in list_all_traces() if row["trace_id"] == trace_id]
        if not traces:
            raise HTTPException(status_code=404, detail=f"no such trace: {trace_id}")

        trace_row = traces[0]
        if not scores:
            return {
                "trace_id": trace_id,
                "capability_id": capability_id,
                "trace": trace_row,
                "eval_status": "pending",
                "passed": None,
                "aggregate_score": None,
                "failure_status": "pending",
                "scores": [],
            }

        passed = all(bool(row["passed"]) for row in scores)
        aggregate = sum(float(row["score"]) for row in scores) / len(scores)
        return {
            "trace_id": trace_id,
            "capability_id": capability_id,
            "trace": trace_row,
            "eval_status": "passed" if passed else "failed",
            "passed": passed,
            "aggregate_score": aggregate,
            "failure_status": "passing" if passed else "failing",
            "scores": [dimension_result(row) for row in scores],
        }

    def dimension_result(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "dimension": row["dimension"],
            "score": float(row["score"]),
            "passed": bool(row["passed"]),
            "reason": row.get("reason", "") or "",
            "evaluator_type": row.get("evaluator_type", "llm_judge") or "llm_judge",
            "evaluator_source": row.get("evaluator_source") or row.get("judge_model", "") or "",
            "ts": row.get("ts", ""),
        }

    def derive_failures(capability_id: str) -> list[dict[str, Any]]:
        capability_store: CapabilityStore = app.state.capability_store
        trace_store: TraceStore = app.state.trace_store
        try:
            spec = capability_store.get(capability_id)
        except CapabilityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"no such capability: {exc}") from exc

        eval_rows = trace_store.list_eval_scores(capability_id=capability_id)
        if not eval_rows:
            return []

        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        for row in eval_rows:
            grouped.setdefault(row["trace_id"], {})
            grouped[row["trace_id"]].setdefault(row["dimension"], row)

        traces = {row["trace_id"]: row for row in list_all_traces(capability_id=capability_id)}
        feedback_by_trace = latest_feedback_by_trace()
        trace_cluster_ids = cluster_ids_by_trace(capability_id)
        trace_review_status = review_status_by_trace(capability_id)

        weights = {dimension.id: float(dimension.weight) for dimension in spec.eval_dimensions}
        failures: list[dict[str, Any]] = []
        for trace_id, dim_rows in grouped.items():
            failing_dimensions = sorted(
                dimension for dimension, row in dim_rows.items() if not bool(row["passed"])
            )
            if not failing_dimensions:
                continue

            trace = traces.get(trace_id, {})
            request_payload = _payload_dict(trace.get("request"))
            response_payload = _payload_dict(trace.get("response"))
            total_weight = 0.0
            weighted_score = 0.0
            for dimension, row in dim_rows.items():
                weight = float(weights.get(dimension, 1.0))
                total_weight += weight
                weighted_score += float(row["score"]) * weight

            feedback = feedback_by_trace.get(trace_id, {})
            corrected_response = feedback.get("corrected_response") or None
            correction_status = "corrected" if corrected_response else "uncorrected"
            correction_source = feedback.get("correction_source") or "human"
            review_status = trace_review_status.get(trace_id, "needs_correction")
            failures.append(
                {
                    "trace_id": trace_id,
                    "project_id": trace.get("project_id")
                    or next(iter(dim_rows.values()))["project_id"],
                    "input": _chat_input_text(request_payload.get("messages", [])),
                    "output": (
                        _messages_output_text(response_payload)
                        if trace.get("method") == "messages"
                        else _chat_output_text(response_payload)
                    ),
                    "context": _flatten_content(request_payload.get("context")),
                    "tags": dict(trace.get("tags") or {}),
                    "ts": trace.get("ts") or max(row.get("ts", "") for row in dim_rows.values()),
                    "aggregate_score": (weighted_score / total_weight) if total_weight else None,
                    "failing_dimensions": failing_dimensions,
                    "corrected_response": corrected_response,
                    "correction_status": correction_status,
                    "correction_source": correction_source if corrected_response else None,
                    "correction_metadata": feedback.get("correction_metadata") or "",
                    "review_status": review_status,
                    "cluster_ids": trace_cluster_ids.get(trace_id, []),
                    "dataset_eligible": correction_status == "corrected"
                    and review_status != "not_useful"
                    and correction_source != "generated",
                    "dimension_results": [
                        dimension_result(row)
                        for row in sorted(
                            dim_rows.values(),
                            key=lambda item: str(item.get("dimension", "")),
                        )
                    ],
                }
            )

        failures.sort(key=lambda row: row.get("ts", ""), reverse=True)
        return failures

    def resolve_failed_traces(capability_id: str, trace_ids: list[str]) -> list[FailedTrace]:
        failure_map = {row["trace_id"]: row for row in derive_failures(capability_id)}
        missing = [trace_id for trace_id in trace_ids if trace_id not in failure_map]
        if missing:
            raise HTTPException(status_code=404, detail=f"no such failures: {', '.join(missing)}")
        return [
            FailedTrace(
                trace_id=row["trace_id"],
                project_id=row["project_id"],
                input=row["input"],
                output=row["output"],
                context=row.get("context") or "",
                corrected_response=row.get("corrected_response"),
                tags=dict(row.get("tags") or {}),
            )
            for row in (failure_map[trace_id] for trace_id in trace_ids)
        ]

    def failed_trace_from_row(row: dict[str, Any]) -> FailedTrace:
        return FailedTrace(
            trace_id=row["trace_id"],
            project_id=row.get("project_id") or "default",
            input=row.get("input") or "",
            output=row.get("output") or "",
            context=row.get("context") or "",
            corrected_response=row.get("corrected_response"),
            tags=dict(row.get("tags") or {}),
        )

    def run_requires_served_validation(run: TrainingRun) -> bool:
        artifact = run.artifact or {}
        return artifact.get("backend") == "mlx-lm" and not bool(artifact.get("dry_run"))

    def run_has_served_validation(run: TrainingRun) -> bool:
        if not run_requires_served_validation(run):
            return True
        return not served_validation_errors(run)

    def served_validation_error_detail(run: TrainingRun) -> str:
        errors = served_validation_errors(run)
        if not errors:
            return ""
        return "served validation proof is incomplete: " + "; ".join(errors)

    def enrich_clusters(
        clusters: list[dict[str, Any]],
        *,
        datasets: list[dict[str, Any]],
        failure_by_trace: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for cluster in clusters:
            trace_ids = [str(trace_id) for trace_id in cluster.get("trace_ids", [])]
            failures = [failure_by_trace[trace_id] for trace_id in trace_ids if trace_id in failure_by_trace]
            corrected = [
                failure
                for failure in failures
                if failure.get("correction_status") == "corrected"
                and failure.get("review_status") != "not_useful"
            ]
            related_datasets = [
                dataset for dataset in datasets if dataset.get("cluster_id") == cluster.get("id")
            ]
            latest_dataset = related_datasets[-1] if related_datasets else None
            row = dict(cluster)
            row["representative_failures"] = failures[:3]
            row["correction_coverage"] = {
                "corrected": len(corrected),
                "total": len(trace_ids),
            }
            row["dataset_eligible"] = bool(trace_ids) and len(corrected) == len(trace_ids)
            row["latest_dataset_id"] = latest_dataset.get("id") if latest_dataset else None
            return_trace_ids = [failure["trace_id"] for failure in failures]
            row["reviewed_trace_ids"] = return_trace_ids
            enriched.append(row)
        return enriched

    def enrich_datasets(
        datasets: list[dict[str, Any]],
        *,
        runs: list[TrainingRun],
        clusters: list[dict[str, Any]],
        failure_by_trace: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        clusters_by_id = {cluster.get("id"): cluster for cluster in clusters}
        rows: list[dict[str, Any]] = []
        for dataset in datasets:
            cluster = clusters_by_id.get(dataset.get("cluster_id"))
            trace_ids = list(cluster.get("trace_ids", [])) if cluster else []
            human_count = sum(
                1
                for trace_id in trace_ids
                if failure_by_trace.get(trace_id, {}).get("correction_status") == "corrected"
                and failure_by_trace.get(trace_id, {}).get("correction_source", "human")
                != "generated"
            )
            generated_count = sum(
                1
                for trace_id in trace_ids
                if failure_by_trace.get(trace_id, {}).get("correction_status") == "corrected"
                and failure_by_trace.get(trace_id, {}).get("correction_source") == "generated"
            )
            row_count = int(dataset.get("row_count") or 0)
            training_run_ids = [run.id for run in runs if run.dataset_id == dataset.get("id")]
            row = dict(dataset)
            row["training_run_ids"] = training_run_ids
            row["correction_source"] = {
                "human": human_count,
                "generated": generated_count
                if generated_count
                else max(row_count - human_count, 0),
            }
            rows.append(row)
        return rows

    def enrich_training_run(run: TrainingRun, *, active_run_id: str | None) -> dict[str, Any]:
        row = _run_to_dict(run)
        row["active"] = run.id == active_run_id
        row["validation_status"] = (
            run.served_validation.get("status")
            if isinstance(run.served_validation, dict)
            else None
        )
        row["gate_status"] = (
            run.gate_verdict.get("decision")
            if isinstance(run.gate_verdict, dict)
            else None
        )
        row["artifact_path"] = (
            run.artifact.get("adapter_dir")
            if isinstance(run.artifact, dict)
            else None
        )
        return row

    def latest_served_validation(runs: list[TrainingRun]) -> dict[str, Any] | None:
        candidates = [run for run in runs if isinstance(run.served_validation, dict)]

        def served_validation_sort_key(run: TrainingRun) -> str:
            validation = run.served_validation
            if not isinstance(validation, dict):
                return str(run.updated_at)
            return str(
                validation.get("finished_at")
                or validation.get("queued_at")
                or run.updated_at
            )

        candidates.sort(
            key=served_validation_sort_key,
            reverse=True,
        )
        if not candidates:
            return None
        run = candidates[0]
        result = dict(run.served_validation or {})
        result["run_id"] = run.id
        return result

    def last_adapted_chat(traces: list[dict[str, Any]]) -> dict[str, Any] | None:
        for trace in traces:
            request_payload = _payload_dict(trace.get("request"))
            if trace.get("provider") == "local-mlx" or request_payload.get("adapters"):
                return {
                    "trace_id": trace["trace_id"],
                    "provider": trace.get("provider"),
                    "model": trace.get("model"),
                    "ts": trace.get("ts"),
                }
        return None

    def latest_before_after(
        runs: list[TrainingRun], *, active_run_id: str | None
    ) -> dict[str, Any] | None:
        ordered = sorted(
            runs,
            key=lambda run: (run.id == active_run_id, run.updated_at),
            reverse=True,
        )
        for run in ordered:
            validation = run.served_validation if isinstance(run.served_validation, dict) else None
            if validation is not None:
                rows = validation.get("rows")
                if isinstance(rows, list) and rows:
                    row = dict(rows[0])
                    return {
                        "run_id": run.id,
                        "trace_id": row.get("trace_id"),
                        "replay_trace_id": row.get("replay_trace_id"),
                        "input": row.get("input"),
                        "baseline_output": row.get("baseline_output"),
                        "adapted_output": row.get("adapted_output") or row.get("served_output"),
                        "evaluator_scores": row.get("scores", []),
                        "adapter_proof": row.get("adapter_proof", {}),
                        "final_verdict": row.get("verdict") or validation.get("status"),
                    }
            if isinstance(run.latest_comparison, dict):
                comparison = dict(run.latest_comparison)
                return {
                    "run_id": run.id,
                    "baseline_output": None,
                    "adapted_output": None,
                    "evaluator_scores": [],
                    "adapter_proof": validation or {},
                    "final_verdict": validation.get("status") if validation else None,
                    "comparison": comparison,
                }
        return None

    def timeline_step(
        step_id: str,
        label: str,
        count: int,
        rows: list[dict[str, Any]],
        href: str,
    ) -> dict[str, Any]:
        latest = max((str(row.get("ts") or row.get("updated_at") or "") for row in rows), default="")
        return {
            "id": step_id,
            "label": label,
            "status": "complete" if count > 0 else "pending",
            "count": count,
            "latest_ts": latest or None,
            "action_needed": None if count > 0 else "No evidence yet",
            "href": href,
        }

    async def maybe_auto_cluster_failures(
        *, specs: list[CapabilitySpec], failed_capability_ids: set[str]
    ) -> dict[str, Any]:
        runtime = local_settings()
        if not runtime.auto_cluster_failures or not failed_capability_ids:
            return {}

        cluster_store: ClusterStore = app.state.cluster_store
        clustered: dict[str, Any] = {}
        for spec in specs:
            if spec.id not in failed_capability_ids:
                continue
            failures = [failed_trace_from_row(row) for row in derive_failures(spec.id)]
            if not failures:
                continue
            result = await cluster_failures(
                capability=spec,
                failures=failures,
                embedder=auto_embedder(ollama_model=runtime.embedding_model),
                llm=judge_client(runtime),
                min_cluster_size=runtime.min_cluster_size,
                summarize=True,
            )
            cluster_store.save(result)
            clustered[spec.id] = result.as_dict()
        return clustered

    def resolve_active_mlx_adapter(capability_ids: list[str]) -> dict[str, str] | None:
        if not capability_ids:
            return None

        settings: Settings = app.state.settings
        adapter_store: AdapterPointerStore = app.state.adapter_pointer_store
        run_store: TrainingRunStore = app.state.training_run_store
        for capability_id in capability_ids:
            active = adapter_store.get(capability_id)
            if active is None:
                continue
            run_id = str(active.get("active_run_id") or "")
            if not run_id:
                continue
            run = run_store.load(run_id)
            artifact = run.artifact if run is not None else None
            if not artifact:
                continue
            if artifact.get("backend") != "mlx-lm" or bool(artifact.get("dry_run")):
                continue

            adapter_dir = str(artifact.get("adapter_dir") or active.get("adapter_dir") or "")
            base_model = str(artifact.get("base_model") or "")
            if not adapter_dir or not base_model:
                continue
            if not settings.mlx_server_url:
                raise HTTPException(
                    status_code=503,
                    detail="active MLX adapter is configured, but FLYCHAIN_MLX_SERVER_URL is not set",
                )
            return {
                "capability_id": capability_id,
                "run_id": run_id,
                "adapter_dir": adapter_dir,
                "base_model": base_model,
            }
        return None

    def resolve_run_mlx_adapter(run: TrainingRun) -> dict[str, str]:
        settings: Settings = app.state.settings
        artifact = run.artifact or {}
        if artifact.get("backend") != "mlx-lm" or bool(artifact.get("dry_run")):
            raise HTTPException(status_code=409, detail="run has no real MLX adapter artifact")
        adapter_dir = str(artifact.get("adapter_dir") or "")
        base_model = str(artifact.get("base_model") or "")
        if not adapter_dir or not base_model:
            raise HTTPException(status_code=409, detail="run artifact is missing adapter metadata")
        if not settings.mlx_server_url:
            raise HTTPException(
                status_code=503,
                detail="FLYCHAIN_MLX_SERVER_URL is required to serve candidate MLX adapters",
            )
        return {
            "capability_id": run.capability_id,
            "run_id": run.id,
            "adapter_dir": adapter_dir,
            "base_model": base_model,
        }

    def resolve_candidate_mlx_adapter(
        candidate_run_id: str, capability_ids: list[str]
    ) -> dict[str, str]:
        run_store: TrainingRunStore = app.state.training_run_store
        run = run_store.load(candidate_run_id)
        if run is None:
            raise HTTPException(
                status_code=404, detail=f"no such candidate run: {candidate_run_id}"
            )
        if run.capability_id not in capability_ids:
            raise HTTPException(
                status_code=400,
                detail=(
                    "candidate validation requires x-flychain-capabilities to include "
                    f"{run.capability_id}"
                ),
            )
        return resolve_run_mlx_adapter(run)

    async def serve_run_adapter_chat(
        *,
        run: TrainingRun,
        body: ChatCompletionRequest,
        project_id: str,
        tags: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        router: ProviderRouter = app.state.router
        store: TraceStore = app.state.trace_store
        registry: ModelRegistry = app.state.registry
        adapter = resolve_run_mlx_adapter(run)
        try:
            resolved = router.resolve_mlx_chat(adapter["base_model"])
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        payload = body.model_dump(exclude_none=True)
        payload["model"] = resolved.model_id
        payload["adapters"] = adapter["adapter_dir"]

        trace_id = _new_id("trace")
        t0 = time.perf_counter()
        status = "ok"
        error = ""
        try:
            result = await resolved.adapter.chat_completions(
                model=resolved.model_id,
                body=payload,
                api_key=resolved.api_key,
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            await store.insert_trace(
                TraceRecord(
                    trace_id=trace_id,
                    project_id=project_id,
                    provider=resolved.provider_name,
                    model=resolved.model_id,
                    method="candidate-run.chat.completions",
                    request=payload,
                    response=None,
                    capability_ids=[run.capability_id],
                    latency_ms=latency_ms,
                    status="error",
                    error=str(exc),
                    tags=tags or {},
                )
            )
            raise HTTPException(status_code=502, detail=f"upstream error: {exc}") from exc

        latency_ms = int((time.perf_counter() - t0) * 1000)
        if result.error:
            status = "error"
            error = result.error

        cost_usd = registry.cost_usd(
            resolved.model_id, result.prompt_tokens, result.completion_tokens
        )
        await store.insert_trace(
            TraceRecord(
                trace_id=trace_id,
                project_id=project_id,
                provider=resolved.provider_name,
                model=resolved.model_id,
                method="candidate-run.chat.completions",
                request=payload,
                response=result.payload,
                capability_ids=[run.capability_id],
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                status=status,
                error=error,
                tags=tags or {},
            )
        )
        return {
            "payload": dict(result.payload),
            "trace_id": trace_id,
            "provider": resolved.provider_name,
            "model": resolved.model_id,
            "adapter_run_id": run.id,
            "adapter_capability_id": run.capability_id,
            "output_text": _chat_output_text(result.payload),
            "raw_status": result.raw_status,
        }

    def resolve_cluster(capability_id: str, cluster_id: str) -> Cluster:
        cluster_store: ClusterStore = app.state.cluster_store
        stored = cluster_store.load(capability_id)
        if stored is None:
            raise HTTPException(status_code=404, detail=f"no stored clusters for {capability_id}")
        for cluster in stored.get("clusters", []):
            if cluster.get("id") == cluster_id:
                return Cluster(
                    id=cluster["id"],
                    capability_id=cluster["capability_id"],
                    label=cluster["label"],
                    size=cluster["size"],
                    trace_ids=list(cluster["trace_ids"]),
                )
        raise HTTPException(status_code=404, detail=f"no such cluster: {cluster_id}")

    def guided_action_id(action_type: str, target_id: str) -> str:
        return f"{action_type}:{target_id}"

    def parse_guided_action_id(action_id: str) -> tuple[str, str]:
        if ":" not in action_id:
            raise HTTPException(status_code=400, detail="guided action id is invalid")
        action_type, target_id = action_id.split(":", 1)
        if not action_type or not target_id:
            raise HTTPException(status_code=400, detail="guided action id is invalid")
        return action_type, target_id

    def guided_dataset_rows(path: str | Path) -> list[dict[str, Any]]:
        dataset_path = Path(path)
        if not dataset_path.exists():
            raise HTTPException(status_code=409, detail=f"dataset file is missing: {path}")
        rows: list[dict[str, Any]] = []
        for line in dataset_path.read_text().splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
        return rows

    def guided_row_prompt(row: dict[str, Any]) -> str:
        prompt = row.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            return prompt.strip()
        messages = row.get("messages")
        if isinstance(messages, list):
            return _chat_input_text(messages)
        return ""

    def guided_row_completion(row: dict[str, Any]) -> str:
        completion = row.get("completion")
        if isinstance(completion, str) and completion.strip():
            return completion.strip()
        chosen = row.get("chosen")
        if isinstance(chosen, str) and chosen.strip():
            return chosen.strip()
        messages = row.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                if isinstance(message, dict) and message.get("role") == "assistant":
                    return _flatten_content(message.get("content"))
        return ""

    def guided_cluster_failures(
        capability_id: str, cluster: Cluster
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        failure_by_trace = {row["trace_id"]: row for row in derive_failures(capability_id)}
        included: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for trace_id in cluster.trace_ids:
            failure = failure_by_trace.get(trace_id)
            if failure is None:
                skipped.append({"trace_id": trace_id, "reason": "missing_failure_evidence"})
                continue
            if failure.get("review_status") == "not_useful":
                skipped.append({"trace_id": trace_id, "reason": "not_useful"})
                continue
            if failure.get("correction_status") != "corrected":
                skipped.append({"trace_id": trace_id, "reason": "missing_human_correction"})
                continue
            if not failure.get("dataset_eligible"):
                skipped.append({"trace_id": trace_id, "reason": "dataset_blocked"})
                continue
            included.append(failure)
        return included, skipped

    def select_guided_recipe(spec: CapabilitySpec, dataset: dict[str, Any]):
        method = str(dataset.get("method") or "sft").lower()
        recipe_refs = [
            str(ref).removesuffix(".yaml")
            for ref in (spec.recipe_refs or [])
            if str(ref).strip()
        ]
        preferred_ids = (
            ["sft-mlx-lora-local-3b", *recipe_refs]
            if method == "sft"
            else recipe_refs
        )
        seen: set[str] = set()
        for recipe_id in preferred_ids:
            if recipe_id in seen:
                continue
            seen.add(recipe_id)
            try:
                recipe = recipe_by_id(recipe_id)
            except KeyError:
                continue
            if str(recipe.method.value) == method:
                return recipe
        for recipe in list_recipes():
            if str(recipe.method.value) == method:
                return recipe
        raise HTTPException(status_code=409, detail=f"no compatible recipe for {method} dataset")

    async def queue_training_run(body: CreateTrainingRun) -> TrainingRun:
        capability_store: CapabilityStore = app.state.capability_store
        dataset_store: DatasetStore = app.state.dataset_store
        run_store: TrainingRunStore = app.state.training_run_store

        try:
            _spec = capability_store.get(body.capability_id)
        except CapabilityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"no such capability: {exc}") from exc

        try:
            recipe = recipe_by_id(body.recipe_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"no such recipe: {exc}") from exc

        dataset_path = dataset_store.resolve_path(body.dataset_id)
        if dataset_path is None:
            raise HTTPException(status_code=404, detail=f"no such dataset: {body.dataset_id}")
        queue = require_job_queue()

        run_id = f"run_{ULID()}"
        now = _now_iso()
        run = TrainingRun(
            id=run_id,
            capability_id=body.capability_id,
            recipe_id=recipe.id,
            dataset_id=body.dataset_id,
            dataset_path=str(dataset_path),
            status="queued",
            created_at=now,
            updated_at=now,
            artifact=None,
            baseline=dict(body.baseline or {}),
            candidate={},
            allow_backend_fallback=body.allow_backend_fallback,
        )
        run_store.save(run)

        job = create_job(
            job_type="training",
            capability_id=run.capability_id,
            dataset_id=run.dataset_id,
            run_id=run.id,
            max_retries=0,
            retry_payload={"function": "run_training_recipe", "kwargs": {"run_id": run.id}},
        )
        job.retry_payload = {
            "function": "run_training_recipe",
            "kwargs": {"run_id": run.id, "job_id": job.id},
        }
        app.state.job_store.save(job)

        try:
            await queue.enqueue_job("run_training_recipe", run_id=run.id, job_id=job.id)
        except Exception as exc:
            app.state.job_store.fail(job.id, error=f"queue enqueue failed: {exc}")
            run.status = "failed"
            run.error = f"queue enqueue failed: {exc}"
            run.updated_at = _now_iso()
            run_store.save(run)
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        return run

    async def queue_served_validation(run_id: str, replay_set_id: str) -> TrainingRun:
        run_store: TrainingRunStore = app.state.training_run_store
        replay_store: ReplaySetStore = app.state.replay_set_store
        run = run_store.load(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no such training run: {run_id}")
        replay_set = replay_store.load(replay_set_id)
        if replay_set is None or replay_set.capability_id != run.capability_id:
            raise HTTPException(status_code=404, detail=f"no such replay set: {replay_set_id}")
        queue = require_job_queue()

        retry_payload: dict[str, Any] = {
            "function": "run_served_validation",
            "kwargs": {"run_id": run.id, "replay_set_id": replay_set_id},
        }
        job = create_job(
            job_type="served_validation",
            capability_id=run.capability_id,
            run_id=run.id,
            replay_set_id=replay_set_id,
            max_retries=1,
            retry_payload=retry_payload,
        )
        retry_payload["kwargs"]["job_id"] = job.id
        job.retry_payload = retry_payload
        app.state.job_store.save(job)

        run.status = "validation-queued"
        run.error = None
        run.served_validation = {
            "status": "queued",
            "replay_set_id": replay_set_id,
            "job_id": job.id,
            "queued_at": _now_iso(),
        }
        run.updated_at = _now_iso()
        run_store.save(run)

        try:
            await queue.enqueue_job(
                "run_served_validation",
                run_id=run.id,
                replay_set_id=replay_set_id,
                job_id=job.id,
            )
        except Exception as exc:
            app.state.job_store.fail(job.id, error=f"queue enqueue failed: {exc}")
            run.status = "validation-failed"
            run.error = f"queue enqueue failed: {exc}"
            run.updated_at = _now_iso()
            run_store.save(run)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return run

    def managed_replay_set_for_run(run: TrainingRun) -> ReplaySet:
        dataset_store: DatasetStore = app.state.dataset_store
        replay_store: ReplaySetStore = app.state.replay_set_store
        dataset_path = dataset_store.resolve_path(run.dataset_id)
        if dataset_path is None:
            raise HTTPException(status_code=404, detail=f"no such dataset: {run.dataset_id}")
        dataset_rows = guided_dataset_rows(dataset_path)
        failure_by_trace = {row["trace_id"]: row for row in derive_failures(run.capability_id)}
        replay_rows: list[dict[str, Any]] = []
        for index, row in enumerate(dataset_rows):
            trace_id = str(row.get("trace_id") or f"{run.dataset_id}:row:{index}")
            prompt = guided_row_prompt(row)
            candidate_output = guided_row_completion(row)
            failure = failure_by_trace.get(trace_id, {})
            replay_rows.append(
                {
                    "trace_id": trace_id,
                    "project_id": failure.get("project_id") or app.state.settings.default_project_id,
                    "input": prompt,
                    "context": failure.get("context") or "",
                    "baseline_output": failure.get("output") or "",
                    "candidate_output": candidate_output,
                    "tags": {
                        "phase": "3",
                        "source": "guided-managed-replay",
                        "dataset_id": run.dataset_id,
                    },
                }
            )

        name = f"Managed validation: {run.dataset_id}"
        existing = next(
            (item for item in replay_store.list_for_capability(run.capability_id) if item.name == name),
            None,
        )
        now = _now_iso()
        replay_set = existing or ReplaySet(
            id=f"replay_{ULID()}",
            capability_id=run.capability_id,
            name=name,
            rows=[],
            created_at=now,
            updated_at=now,
        )
        replay_set.rows = replay_rows
        replay_set.updated_at = now
        if not replay_set.created_at:
            replay_set.created_at = now
        replay_store.save(replay_set)
        return replay_set

    async def post_activation_check(capability_id: str, run: TrainingRun) -> dict[str, Any]:
        validation = run.served_validation if isinstance(run.served_validation, dict) else {}
        row = next(iter(validation.get("rows") or []), None)
        prompt = str((row or {}).get("input") or "")
        context = str((row or {}).get("context") or "")
        if not prompt and run.dataset_path:
            dataset_rows = guided_dataset_rows(run.dataset_path)
            if dataset_rows:
                prompt = guided_row_prompt(dataset_rows[0])
        if not prompt:
            return {"status": "failed", "proof_errors": ["missing validation prompt"]}

        messages: list[ChatMessage] = []
        if context:
            messages.append(ChatMessage(role="system", content=context))
        messages.append(ChatMessage(role="user", content=prompt))
        request_body = ChatCompletionRequest(
            model=str((run.artifact or {}).get("base_model") or ""),
            messages=messages,
            stream=False,
        ).model_dump(exclude_none=True)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://flychain-internal"
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json=request_body,
                headers={
                    "x-flychain-project": app.state.settings.default_project_id,
                    "x-flychain-capabilities": capability_id,
                    "x-flychain-tags": "phase=3,source=guided-post-activation",
                },
            )
        response.raise_for_status()
        payload = response.json()
        adapter_run_id = response.headers.get("x-flychain-active-adapter-run-id", "")
        adapter_capability_id = response.headers.get(
            "x-flychain-active-adapter-capability-id", ""
        )
        routing_mode = response.headers.get("x-flychain-adapter-routing-mode", "")
        trace_id = response.headers.get("x-flychain-trace-id", "")
        proof_errors = []
        if not trace_id:
            proof_errors.append("missing trace id")
        if adapter_run_id != run.id:
            proof_errors.append("wrong adapter run id")
        if adapter_capability_id != capability_id:
            proof_errors.append("wrong adapter capability id")
        if routing_mode != "active":
            proof_errors.append("wrong adapter routing mode")
        return {
            "status": "failed" if proof_errors else "passed",
            "trace_id": trace_id,
            "provider": response.headers.get("x-flychain-provider", ""),
            "model": response.headers.get("x-flychain-model", ""),
            "adapter_run_id": adapter_run_id,
            "adapter_capability_id": adapter_capability_id,
            "routing_mode": routing_mode,
            "proof_errors": proof_errors,
            "output": _chat_output_text(payload),
        }

    def guided_action(
        *,
        action_type: str,
        target_id: str,
        status: str,
        requires_approval: bool,
        reason: str,
        blocked_reasons: list[str] | None = None,
        preview: dict[str, Any] | None = None,
        default_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": guided_action_id(action_type, target_id),
            "type": action_type,
            "target_id": target_id,
            "status": status,
            "requires_approval": requires_approval,
            "reason": reason,
            "blocked_reasons": blocked_reasons or [],
            "preview": preview or {},
            "default_params": default_params or {},
        }

    def build_guided_actions(capability_id: str) -> dict[str, Any]:
        capability_store: CapabilityStore = app.state.capability_store
        cluster_store: ClusterStore = app.state.cluster_store
        dataset_store: DatasetStore = app.state.dataset_store
        run_store: TrainingRunStore = app.state.training_run_store
        adapter_store: AdapterPointerStore = app.state.adapter_pointer_store
        try:
            spec = capability_store.get(capability_id)
        except CapabilityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"no such capability: {exc}") from exc

        runtime = local_settings()
        threshold = int(runtime.min_cluster_size)
        clusters_data = cluster_store.load(capability_id) or {
            "capability_id": capability_id,
            "clusters": [],
            "noise_trace_ids": [],
        }
        datasets = dataset_store.list_for_capability(capability_id)
        runs = run_store.list_for_capability(capability_id)
        active_adapter = adapter_store.get(capability_id)
        active_run_id = active_adapter.get("active_run_id") if active_adapter else None
        actions: list[dict[str, Any]] = []

        for cluster_row in clusters_data.get("clusters", []):
            cluster = Cluster(
                id=cluster_row["id"],
                capability_id=cluster_row["capability_id"],
                label=cluster_row["label"],
                size=cluster_row["size"],
                trace_ids=list(cluster_row["trace_ids"]),
            )
            included, skipped = guided_cluster_failures(capability_id, cluster)
            downstream = [dataset for dataset in datasets if dataset.get("cluster_id") == cluster.id]
            blocked = []
            if len(included) < threshold:
                blocked.append(
                    f"needs {threshold} corrected eligible failures; found {len(included)}"
                )
            status = "complete" if downstream else ("blocked" if blocked else "available")
            actions.append(
                guided_action(
                    action_type="create_dataset",
                    target_id=cluster.id,
                    status=status,
                    requires_approval=False,
                    reason=(
                        "Dataset already exists for this cluster"
                        if downstream
                        else "Create an SFT dataset from human corrections only"
                    ),
                    blocked_reasons=[] if downstream else blocked,
                    preview={
                        "cluster_id": cluster.id,
                        "label": cluster.label,
                        "method": "sft",
                        "included_count": len(included),
                        "included_trace_ids": [row["trace_id"] for row in included],
                        "skipped_count": len(skipped),
                        "skipped": skipped,
                        "downstream_dataset_ids": [dataset["id"] for dataset in downstream],
                    },
                    default_params={"method": "sft", "generate_missing": False},
                )
            )

        for dataset in datasets:
            row_count = int(dataset.get("row_count") or 0)
            related_runs = [run for run in runs if run.dataset_id == dataset.get("id")]
            recipe = select_guided_recipe(spec, dataset)
            correction_source = enrich_datasets(
                [dataset],
                runs=runs,
                clusters=clusters_data.get("clusters", []),
                failure_by_trace={row["trace_id"]: row for row in derive_failures(capability_id)},
            )[0]["correction_source"]
            blocked = []
            if row_count < threshold:
                blocked.append(f"needs at least {threshold} dataset rows; found {row_count}")
            if int(correction_source.get("generated") or 0) > 0:
                blocked.append("dataset includes generated corrections")
            status = "complete" if related_runs else ("blocked" if blocked else "available")
            actions.append(
                guided_action(
                    action_type="start_training",
                    target_id=str(dataset["id"]),
                    status=status,
                    requires_approval=True,
                    reason=(
                        "Training run already exists for this dataset"
                        if related_runs
                        else "Queue one training run after inline approval"
                    ),
                    blocked_reasons=[] if related_runs else blocked,
                    preview={
                        "dataset_id": dataset["id"],
                        "dataset_path": dataset.get("path"),
                        "row_count": row_count,
                        "recipe_id": recipe.id,
                        "recipe_backend": recipe.backend.value,
                        "recipe_method": recipe.method.value,
                        "mlx_health": _http_health(
                            "MLX server",
                            app.state.settings.mlx_server_url,
                            "/v1/models",
                        ),
                        "allow_backend_fallback": False,
                        "downstream_run_ids": [run.id for run in related_runs],
                    },
                    default_params={
                        "recipe_id": recipe.id,
                        "allow_backend_fallback": False,
                    },
                )
            )

        for run in runs:
            artifact = run.artifact or {}
            validation = run.served_validation if isinstance(run.served_validation, dict) else {}
            blocked = []
            if run.status not in {"trained", "validation-failed"}:
                blocked.append(f"run status is {run.status}")
            if artifact.get("backend") != "mlx-lm" or bool(artifact.get("dry_run")):
                blocked.append("run has no real MLX adapter artifact")
            if not artifact.get("adapter_dir") or not artifact.get("base_model"):
                blocked.append("run artifact is missing adapter metadata")
            validation_status = str(validation.get("status") or "")
            if validation_status == "passed":
                status = "complete"
            elif validation_status in {"queued", "running"} or run.status in {
                "validation-queued",
                "validation-running",
            }:
                status = "running"
            else:
                status = "blocked" if blocked else "available"
            actions.append(
                guided_action(
                    action_type="run_served_validation",
                    target_id=run.id,
                    status=status,
                    requires_approval=False,
                    reason=(
                        "Served validation already passed"
                        if status == "complete"
                        else "Run served validation through the real chat-serving path"
                    ),
                    blocked_reasons=[] if status in {"complete", "running"} else blocked,
                    preview={
                        "run_id": run.id,
                        "dataset_id": run.dataset_id,
                        "artifact_backend": artifact.get("backend"),
                        "artifact_dry_run": bool(artifact.get("dry_run")),
                        "served_validation_status": validation_status or None,
                        "managed_replay_name": f"Managed validation: {run.dataset_id}",
                    },
                    default_params={"managed_replay": True},
                )
            )

            promotion_blocked = []
            if run.id == active_run_id:
                promotion_status = "complete"
            else:
                if run.status not in {"validated", "promoted"}:
                    promotion_blocked.append(f"run status is {run.status}")
                if not run_requires_served_validation(run):
                    promotion_blocked.append("run has no real served adapter artifact")
                elif not run_has_served_validation(run):
                    promotion_blocked.append(
                        served_validation_error_detail(run)
                        or "served validation has not passed"
                    )
                promotion_status = "blocked" if promotion_blocked else "available"
            actions.append(
                guided_action(
                    action_type="promote_adapter",
                    target_id=run.id,
                    status=promotion_status,
                    requires_approval=True,
                    reason=(
                        "Run is already active"
                        if promotion_status == "complete"
                        else "Promote this validated adapter after inline approval"
                    ),
                    blocked_reasons=[] if promotion_status == "complete" else promotion_blocked,
                    preview={
                        "current_active_adapter": active_adapter,
                        "candidate_run_id": run.id,
                        "validation_score": validation.get("aggregate_score"),
                        "validation_status": validation.get("status"),
                        "adapter_proof": {
                            "adapter_run_id": validation.get("adapter_run_id"),
                            "adapter_capability_id": validation.get("adapter_capability_id"),
                            "routing_mode": validation.get("routing_mode"),
                        },
                    },
                    default_params={"replace_active": True},
                )
            )

        order = {
            "create_dataset": 0,
            "start_training": 1,
            "run_served_validation": 2,
            "promote_adapter": 3,
        }
        status_order = {"available": 0, "running": 1, "blocked": 2, "complete": 3}
        actions.sort(
            key=lambda action: (
                order.get(str(action["type"]), 99),
                status_order.get(str(action["status"]), 99),
                str(action["target_id"]),
            )
        )
        return {
            "capability_id": capability_id,
            "readiness": {
                "min_cluster_size": threshold,
                "active_adapter_run_id": active_run_id,
            },
            "thresholds": {"min_corrected_failures": threshold},
            "active_adapter": {"capability_id": capability_id, "active": active_adapter},
            "actions": actions,
        }

    def autopilot_policy(capability_id: str) -> AutopilotPolicy:
        store: AutopilotStore = app.state.autopilot_store
        return store.load_policy(capability_id, threshold=int(local_settings().min_cluster_size))

    def autopilot_counts(
        capability_id: str, *, policy: AutopilotPolicy | None = None
    ) -> dict[str, int]:
        cluster_store: ClusterStore = app.state.cluster_store
        dataset_store: DatasetStore = app.state.dataset_store
        run_store: TrainingRunStore = app.state.training_run_store
        clusters = cluster_store.load(capability_id) or {"clusters": []}
        failures = derive_failures(capability_id)

        def is_dataset_eligible(row: dict[str, Any]) -> bool:
            if row.get("correction_status") != "corrected":
                return False
            if row.get("review_status") == "not_useful":
                return False
            if row.get("correction_source") == "generated":
                return bool(policy and policy.allow_generated_corrections)
            return True

        return {
            "failures": len(failures),
            "corrected_failures": len(
                [row for row in failures if row.get("correction_status") == "corrected"]
            ),
            "eligible_failures": len([row for row in failures if is_dataset_eligible(row)]),
            "clusters": len(clusters.get("clusters", [])),
            "datasets": len(dataset_store.list_for_capability(capability_id)),
            "training_runs": len(run_store.list_for_capability(capability_id)),
        }

    def autopilot_response(status: str, decision: dict[str, Any]) -> dict[str, Any]:
        return {"status": status, "decision": decision}

    def deterministic_expected_correction(
        spec: CapabilitySpec, failure: dict[str, Any]
    ) -> str | None:
        failing = set(failure.get("failing_dimensions") or [])
        for dimension in spec.eval_dimensions:
            if dimension.id not in failing:
                continue
            evaluator = getattr(dimension, "evaluator", None)
            deterministic = getattr(evaluator, "deterministic", None)
            if str(getattr(evaluator, "mode", "")) != "EvaluatorMode.DETERMINISTIC" and str(
                getattr(evaluator, "mode", "")
            ) != "deterministic":
                continue
            if str(getattr(deterministic, "type", "")) not in {
                "DeterministicEvaluatorType.EXACT_MATCH",
                "exact_match",
            }:
                continue
            expected = getattr(deterministic, "expected", None)
            if isinstance(expected, str) and expected.strip():
                return expected
        return None

    async def generate_autopilot_corrections(
        capability_id: str,
        *,
        spec: CapabilitySpec,
        policy: AutopilotPolicy,
        trigger: str,
    ) -> int:
        trace_store: TraceStore = app.state.trace_store
        generated = 0
        for failure in derive_failures(capability_id):
            if failure.get("correction_status") == "corrected":
                continue
            correction = deterministic_expected_correction(spec, failure)
            if correction is None:
                correction = str(failure.get("corrected_response") or "").strip()
            if not correction:
                continue
            await trace_store.insert_feedback(
                feedback_id=_new_id("fb"),
                trace_id=str(failure["trace_id"]),
                project_id=str(failure.get("project_id") or app.state.settings.default_project_id),
                thumb="down",
                score=-1,
                comment="autopilot generated correction",
                corrected_response=correction,
                correction_source="generated",
                correction_metadata={"policy_version": policy.version, "trigger": trigger},
            )
            generated += 1
        return generated

    def autopilot_cluster_failures(
        capability_id: str, cluster: Cluster, policy: AutopilotPolicy
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        failure_by_trace = {row["trace_id"]: row for row in derive_failures(capability_id)}
        included: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for trace_id in cluster.trace_ids:
            failure = failure_by_trace.get(trace_id)
            if failure is None:
                skipped.append({"trace_id": trace_id, "reason": "missing_failure_evidence"})
                continue
            if failure.get("review_status") == "not_useful":
                skipped.append({"trace_id": trace_id, "reason": "not_useful"})
                continue
            if failure.get("correction_status") != "corrected":
                skipped.append({"trace_id": trace_id, "reason": "missing_correction"})
                continue
            if failure.get("correction_source") == "generated" and not policy.allow_generated_corrections:
                skipped.append(
                    {"trace_id": trace_id, "reason": "generated_correction_not_allowed"}
                )
                continue
            included.append(failure)
        return included, skipped

    async def autopilot_cluster_if_needed(
        capability_id: str,
        *,
        spec: CapabilitySpec,
        policy: AutopilotPolicy,
        trigger: str,
    ) -> None:
        cluster_store: ClusterStore = app.state.cluster_store
        existing = cluster_store.load(capability_id) or {}
        if existing.get("clusters"):
            return
        failures = [failed_trace_from_row(row) for row in derive_failures(capability_id)]
        if len(failures) < int(policy.min_cluster_size):
            return
        runtime = local_settings()
        result = await cluster_failures(
            capability=spec,
            failures=failures,
            embedder=auto_embedder(ollama_model=runtime.embedding_model),
            llm=judge_client(runtime),
            min_cluster_size=int(policy.min_cluster_size),
            summarize=True,
        )
        cluster_store.save(result)
        store: AutopilotStore = app.state.autopilot_store
        store.append_decision(
            capability_id,
            trigger=trigger,
            policy_version=policy.version,
            action="cluster_failures",
            outcome="complete",
            input_counts=autopilot_counts(capability_id, policy=policy),
            result={"cluster_count": len(result.clusters)},
        )

    def training_runs_today(capability_id: str) -> int:
        today = _now_iso()[:10]
        run_store: TrainingRunStore = app.state.training_run_store
        return len(
            [
                run
                for run in run_store.list_for_capability(capability_id)
                if str(run.created_at).startswith(today)
            ]
        )

    def promotion_cooldown_remaining(capability_id: str, policy: AutopilotPolicy) -> int:
        cooldown = int(policy.promotion_cooldown_seconds or 0)
        if cooldown <= 0:
            return 0
        from datetime import UTC, datetime

        store: AutopilotStore = app.state.autopilot_store
        now = datetime.now(UTC)
        for row in store.list_audit(capability_id, limit=500):
            if row.get("action") != "promote_adapter" or row.get("outcome") != "complete":
                continue
            ts = str(row.get("approved_at") or row.get("updated_at") or row.get("created_at") or "")
            try:
                promoted_at = datetime.fromisoformat(ts)
            except ValueError:
                continue
            if promoted_at.tzinfo is None:
                promoted_at = promoted_at.replace(tzinfo=UTC)
            remaining = cooldown - int((now - promoted_at).total_seconds())
            if remaining > 0:
                return remaining
        return 0

    async def autopilot_create_dataset_for_cluster(
        capability_id: str,
        *,
        spec: CapabilitySpec,
        cluster: Cluster,
        policy: AutopilotPolicy,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        dataset_store: DatasetStore = app.state.dataset_store
        included, skipped = autopilot_cluster_failures(capability_id, cluster, policy)
        included_trace_ids = [row["trace_id"] for row in included]
        cluster_for_dataset = Cluster(
            id=cluster.id,
            capability_id=cluster.capability_id,
            label=cluster.label,
            size=len(included_trace_ids),
            trace_ids=included_trace_ids,
        )
        rows = await synthesize_sft_dataset(
            capability=spec,
            cluster=cluster_for_dataset,
            failures=[failed_trace_from_row(row) for row in included],
            llm=None,
            generate_missing=False,
        )
        dataset_id = f"ds_{ULID()}"
        out_dir = default_data_dir() / "datasets" / capability_id
        out_path = out_dir / f"{dataset_id}.jsonl"
        write_jsonl(out_path, rows)
        record = SynthesizedDataset(
            id=dataset_id,
            capability_id=capability_id,
            cluster_id=cluster.id,
            method="sft",
            path=str(out_path),
            row_count=len(rows),
        )
        dataset_store.record(record)
        return asdict(record), skipped

    def promote_run_for_autopilot(
        capability_id: str,
        run: TrainingRun,
        *,
        reason: str,
        decision_id: str | None,
    ) -> dict[str, Any]:
        adapter_store: AdapterPointerStore = app.state.adapter_pointer_store
        autopilot_store: AutopilotStore = app.state.autopilot_store
        run_store: TrainingRunStore = app.state.training_run_store
        previous = adapter_store.get(capability_id)
        adapter_store.set_active(
            capability_id,
            run_id=run.id,
            adapter_dir=str((run.artifact or {}).get("adapter_dir", "")),
            baseline=run.baseline,
            candidate=run.candidate,
        )
        current = adapter_store.get(capability_id)
        autopilot_store.record_adapter_history(
            capability_id,
            previous=previous,
            current=current,
            reason=reason,
            decision_id=decision_id,
        )
        run.status = "promoted"
        run.updated_at = _now_iso()
        run_store.save(run)
        return {"capability_id": capability_id, "active_run_id": run.id}

    async def run_autopilot(capability_id: str, *, trigger: str) -> dict[str, Any]:
        capability_store: CapabilityStore = app.state.capability_store
        autopilot_store: AutopilotStore = app.state.autopilot_store
        cluster_store: ClusterStore = app.state.cluster_store
        dataset_store: DatasetStore = app.state.dataset_store
        run_store: TrainingRunStore = app.state.training_run_store
        adapter_store: AdapterPointerStore = app.state.adapter_pointer_store
        try:
            spec = capability_store.get(capability_id)
        except CapabilityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"no such capability: {exc}") from exc

        policy = autopilot_policy(capability_id)
        if not policy.enabled:
            decision = autopilot_store.append_decision(
                capability_id,
                trigger=trigger,
                policy_version=policy.version,
                action="none",
                outcome="skipped",
                reasons=["policy disabled"],
                input_counts=autopilot_counts(capability_id, policy=policy),
            )
            return autopilot_response("skipped", decision.as_dict())

        if policy.auto_generate_corrections:
            generated = await generate_autopilot_corrections(
                capability_id, spec=spec, policy=policy, trigger=trigger
            )
            if generated:
                autopilot_store.append_decision(
                    capability_id,
                    trigger=trigger,
                    policy_version=policy.version,
                    action="generate_corrections",
                    outcome="complete",
                    input_counts=autopilot_counts(capability_id, policy=policy),
                    result={"generated": generated},
                )

        await autopilot_cluster_if_needed(
            capability_id, spec=spec, policy=policy, trigger=trigger
        )

        clusters_data = cluster_store.load(capability_id) or {
            "capability_id": capability_id,
            "clusters": [],
        }
        datasets = dataset_store.list_for_capability(capability_id)
        runs = run_store.list_for_capability(capability_id)
        active = adapter_store.get(capability_id)
        active_run_id = active.get("active_run_id") if active else None

        for cluster_row in clusters_data.get("clusters", []):
            cluster = Cluster(
                id=cluster_row["id"],
                capability_id=cluster_row["capability_id"],
                label=cluster_row["label"],
                size=cluster_row["size"],
                trace_ids=list(cluster_row["trace_ids"]),
            )
            if any(dataset.get("cluster_id") == cluster.id for dataset in datasets):
                continue
            included, skipped = autopilot_cluster_failures(capability_id, cluster, policy)
            reasons = []
            if len(included) < int(policy.min_corrected_failures):
                reasons.append(
                    f"needs {policy.min_corrected_failures} corrected eligible failures; found {len(included)}"
                )
            if skipped and all(row["reason"] == "generated_correction_not_allowed" for row in skipped):
                reasons.append("generated corrections are not dataset eligible")
            if not policy.auto_create_dataset:
                reasons.append("auto dataset creation disabled")
            if reasons:
                decision = autopilot_store.append_decision(
                    capability_id,
                    trigger=trigger,
                    policy_version=policy.version,
                    action="create_dataset",
                    outcome="blocked",
                    reasons=reasons,
                    input_counts=autopilot_counts(capability_id, policy=policy),
                    target_id=cluster.id,
                    result={"skipped": skipped},
                )
                return autopilot_response("blocked", decision.as_dict())
            dataset, skipped = await autopilot_create_dataset_for_cluster(
                capability_id, spec=spec, cluster=cluster, policy=policy
            )
            decision = autopilot_store.append_decision(
                capability_id,
                trigger=trigger,
                policy_version=policy.version,
                action="create_dataset",
                outcome="complete",
                input_counts=autopilot_counts(capability_id, policy=policy),
                target_id=cluster.id,
                result={
                    "dataset_id": dataset["id"],
                    "row_count": dataset["row_count"],
                    "skipped": skipped,
                },
            )
            datasets = dataset_store.list_for_capability(capability_id)
            runs = run_store.list_for_capability(capability_id)

        for dataset in datasets:
            related_runs = [run for run in runs if run.dataset_id == dataset.get("id")]
            if related_runs:
                continue
            reasons = []
            if not policy.auto_start_training:
                reasons.append("auto training disabled")
            if int(dataset.get("row_count") or 0) < int(policy.min_corrected_failures):
                reasons.append(
                    f"needs at least {policy.min_corrected_failures} dataset rows; found {int(dataset.get('row_count') or 0)}"
                )
            if training_runs_today(capability_id) >= int(policy.max_training_runs_per_day):
                reasons.append("training rate limit reached")
            recipe = select_guided_recipe(spec, dataset)
            if recipe.id not in set(policy.allowed_training_recipes):
                reasons.append(f"recipe not allowed by policy: {recipe.id}")
            if reasons:
                decision = autopilot_store.append_decision(
                    capability_id,
                    trigger=trigger,
                    policy_version=policy.version,
                    action="start_training",
                    outcome="blocked",
                    reasons=reasons,
                    input_counts=autopilot_counts(capability_id, policy=policy),
                    target_id=str(dataset["id"]),
                )
                return autopilot_response("blocked", decision.as_dict())
            run = await queue_training_run(
                CreateTrainingRun(
                    capability_id=capability_id,
                    recipe_id=recipe.id,
                    dataset_id=str(dataset["id"]),
                    allow_backend_fallback=bool(policy.allow_dry_run_fallback),
                )
            )
            job_ids = [
                job.id
                for job in app.state.job_store.list()
                if job.run_id == run.id and job.type == "training"
            ][:1]
            decision = autopilot_store.append_decision(
                capability_id,
                trigger=trigger,
                policy_version=policy.version,
                action="start_training",
                outcome="running",
                input_counts=autopilot_counts(capability_id, policy=policy),
                target_id=run.id,
                job_ids=job_ids,
                result={"run_id": run.id, "dataset_id": run.dataset_id},
            )
            return autopilot_response("running", decision.as_dict())

        for run in runs:
            validation = run.served_validation if isinstance(run.served_validation, dict) else {}
            if validation.get("status") in {"passed", "queued", "running"}:
                continue
            if run.status not in {"trained", "validation-failed"}:
                continue
            reasons = []
            if not policy.auto_run_served_validation:
                reasons.append("auto served validation disabled")
            artifact = run.artifact or {}
            if artifact.get("backend") != "mlx-lm" or bool(artifact.get("dry_run")):
                reasons.append("run has no real MLX adapter artifact")
            if reasons:
                decision = autopilot_store.append_decision(
                    capability_id,
                    trigger=trigger,
                    policy_version=policy.version,
                    action="run_served_validation",
                    outcome="blocked",
                    reasons=reasons,
                    input_counts=autopilot_counts(capability_id, policy=policy),
                    target_id=run.id,
                )
                return autopilot_response("blocked", decision.as_dict())
            replay_set = managed_replay_set_for_run(run)
            queued = await queue_served_validation(run.id, replay_set.id)
            validation = queued.served_validation or {}
            decision = autopilot_store.append_decision(
                capability_id,
                trigger=trigger,
                policy_version=policy.version,
                action="run_served_validation",
                outcome="running",
                input_counts=autopilot_counts(capability_id, policy=policy),
                target_id=run.id,
                job_ids=[str(validation.get("job_id"))] if validation.get("job_id") else [],
                result={"run_id": run.id, "replay_set_id": replay_set.id},
            )
            return autopilot_response("running", decision.as_dict())

        for run in runs:
            if run.id == active_run_id:
                continue
            if run.status not in {"validated", "promoted"}:
                continue
            reasons = []
            if policy.require_served_validation and not run_has_served_validation(run):
                reasons.append(
                    served_validation_error_detail(run) or "served validation has not passed"
                )
            cooldown_remaining = promotion_cooldown_remaining(capability_id, policy)
            if cooldown_remaining > 0:
                reasons.append(f"promotion cooldown active for {cooldown_remaining}s")
            if reasons:
                decision = autopilot_store.append_decision(
                    capability_id,
                    trigger=trigger,
                    policy_version=policy.version,
                    action="promote_adapter",
                    outcome="blocked",
                    reasons=reasons,
                    input_counts=autopilot_counts(capability_id, policy=policy),
                    target_id=run.id,
                )
                return autopilot_response("blocked", decision.as_dict())
            if policy.require_promotion_approval or not policy.auto_promote:
                decision = autopilot_store.append_decision(
                    capability_id,
                    trigger=trigger,
                    policy_version=policy.version,
                    action="promote_adapter",
                    outcome="approval_required",
                    reasons=["promotion approval required"],
                    input_counts=autopilot_counts(capability_id, policy=policy),
                    target_id=run.id,
                    approval_status="pending",
                    result={
                        "candidate_run_id": run.id,
                        "validation": run.served_validation or {},
                        "current_active_adapter": active,
                    },
                )
                return autopilot_response("approval_required", decision.as_dict())
            result = promote_run_for_autopilot(
                capability_id, run, reason="autopilot auto-promote", decision_id=None
            )
            decision = autopilot_store.append_decision(
                capability_id,
                trigger=trigger,
                policy_version=policy.version,
                action="promote_adapter",
                outcome="complete",
                input_counts=autopilot_counts(capability_id, policy=policy),
                target_id=run.id,
                result=result,
            )
            return autopilot_response("complete", decision.as_dict())

        decision = autopilot_store.append_decision(
            capability_id,
            trigger=trigger,
            policy_version=policy.version,
            action="none",
            outcome="complete",
            input_counts=autopilot_counts(capability_id, policy=policy),
            reasons=["no autopilot action available"],
        )
        return autopilot_response("complete", decision.as_dict())

    async def maybe_run_enabled_autopilot(
        capability_ids: list[str] | set[str], *, trigger: str
    ) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for capability_id in sorted({str(item) for item in capability_ids if str(item).strip()}):
            try:
                if not autopilot_policy(capability_id).enabled:
                    continue
                results[capability_id] = await run_autopilot(capability_id, trigger=trigger)
            except Exception as exc:  # pragma: no cover - defensive trigger isolation
                logger.warning("autopilot trigger %s failed for %s: %s", trigger, capability_id, exc)
        return results

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/version")
    def version() -> dict[str, str]:
        settings: Settings = app.state.settings
        return {"version": __version__, "env": settings.env}

    @app.get("/v1/models")
    def list_models() -> dict[str, Any]:
        registry: ModelRegistry = app.state.registry
        providers = {}
        for name in registry.providers():
            base_url = registry.provider_base_url(name)
            providers[name] = {"base_url": base_url}
        return {"providers": providers}

    @app.post("/v1/chat/completions")
    async def chat_completions(
        req: Request,
        body: ChatCompletionRequest,
        x_flychain_project: str | None = Header(default=None, alias="x-flychain-project"),
        x_flychain_capabilities: str | None = Header(default=None, alias="x-flychain-capabilities"),
        x_flychain_tags: str | None = Header(default=None, alias="x-flychain-tags"),
        x_flychain_candidate_run_id: str | None = Header(
            default=None, alias="x-flychain-candidate-run-id"
        ),
    ) -> Response:
        if body.stream:
            # Phase 1 returns a clear error for streaming; streaming lands later.
            raise HTTPException(
                status_code=400,
                detail="streaming is not yet supported on the FlyChain gateway",
            )

        settings: Settings = app.state.settings
        router: ProviderRouter = app.state.router
        store: TraceStore = app.state.trace_store
        registry: ModelRegistry = app.state.registry

        project_id, capability_ids, tags = _extract_headers(
            x_flychain_project=x_flychain_project,
            x_flychain_capabilities=x_flychain_capabilities,
            x_flychain_tags=x_flychain_tags,
            default_project_id=settings.default_project_id,
        )

        adapter_proof: dict[str, str] | None = None
        try:
            if x_flychain_candidate_run_id:
                adapter_proof = resolve_candidate_mlx_adapter(
                    x_flychain_candidate_run_id, capability_ids
                )
                adapter_proof["routing_mode"] = "candidate"
                resolved = router.resolve_mlx_chat(adapter_proof["base_model"])
            else:
                active_adapter = resolve_active_mlx_adapter(capability_ids)
                if active_adapter is not None:
                    adapter_proof = dict(active_adapter)
                    adapter_proof["routing_mode"] = "active"
                    resolved = router.resolve_mlx_chat(adapter_proof["base_model"])
                else:
                    resolved = router.resolve_chat(body.model)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (ModelNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        payload = body.model_dump(exclude_none=True)
        payload["model"] = resolved.model_id
        if adapter_proof is not None:
            payload["adapters"] = adapter_proof["adapter_dir"]

        trace_id = _new_id("trace")
        tracer = get_tracer()
        t0 = time.perf_counter()
        status = "ok"
        error = ""

        with tracer.start_as_current_span(f"chat.completions/{resolved.provider_name}") as span:
            span.set_attribute("flychain.trace_id", trace_id)
            span.set_attribute("flychain.project_id", project_id)
            try:
                result = await resolved.adapter.chat_completions(
                    model=resolved.model_id,
                    body=payload,
                    api_key=resolved.api_key,
                )
            except Exception as exc:  # pragma: no cover - exercised via integration
                span.record_exception(exc)
                latency_ms = int((time.perf_counter() - t0) * 1000)
                await store.insert_trace(
                    TraceRecord(
                        trace_id=trace_id,
                        project_id=project_id,
                        provider=resolved.provider_name,
                        model=resolved.model_id,
                        method="chat.completions",
                        request=payload,
                        response=None,
                        capability_ids=capability_ids,
                        latency_ms=latency_ms,
                        status="error",
                        error=str(exc),
                        tags=tags,
                    )
                )
                raise HTTPException(status_code=502, detail=f"upstream error: {exc}") from exc

            latency_ms = int((time.perf_counter() - t0) * 1000)
            if result.error:
                status = "error"
                error = result.error
                span.set_status(otel_trace.Status(otel_trace.StatusCode.ERROR, error))

            cost_usd = registry.cost_usd(
                resolved.model_id, result.prompt_tokens, result.completion_tokens
            )

            span.set_attributes(
                make_llm_attributes(
                    provider=resolved.provider_name,
                    model=resolved.model_id,
                    method="chat.completions",
                    request_payload=payload,
                    response_payload=result.payload,
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    total_tokens=result.total_tokens,
                    latency_ms=latency_ms,
                    project_id=project_id,
                )
            )

            await store.insert_trace(
                TraceRecord(
                    trace_id=trace_id,
                    project_id=project_id,
                    provider=resolved.provider_name,
                    model=resolved.model_id,
                    method="chat.completions",
                    request=payload,
                    response=result.payload,
                    capability_ids=capability_ids,
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    total_tokens=result.total_tokens,
                    cost_usd=cost_usd,
                    latency_ms=latency_ms,
                    status=status,
                    error=error,
                    tags=tags,
                )
            )
            await maybe_enqueue_auto_eval(
                trace_id=trace_id,
                project_id=project_id,
                input_text=_chat_input_text(body.messages),
                output_text=_chat_output_text(result.payload),
                tags=tags,
                capability_ids=capability_ids,
            )

        response_body = dict(result.payload)
        response_body.setdefault("id", trace_id)
        extra_headers = {
            "x-flychain-trace-id": trace_id,
            "x-flychain-provider": resolved.provider_name,
            "x-flychain-model": resolved.model_id,
        }
        if adapter_proof is not None:
            extra_headers.update(
                {
                    "x-flychain-adapter-run-id": adapter_proof["run_id"],
                    "x-flychain-adapter-capability-id": adapter_proof["capability_id"],
                    "x-flychain-adapter-routing-mode": adapter_proof["routing_mode"],
                }
            )
        if adapter_proof is not None and adapter_proof.get("routing_mode") == "active":
            extra_headers.update(
                {
                    "x-flychain-active-adapter-run-id": adapter_proof["run_id"],
                    "x-flychain-active-adapter-capability-id": adapter_proof["capability_id"],
                }
            )
        return _json_response(
            response_body,
            status_code=result.raw_status if result.raw_status < 400 else 502,
            extra_headers=extra_headers,
        )

    @app.post("/internal/training-runs/{run_id}/chat/completions")
    async def internal_run_chat_completions(
        run_id: str,
        body: ChatCompletionRequest,
        x_flychain_project: str | None = Header(default=None, alias="x-flychain-project"),
        x_flychain_tags: str | None = Header(default=None, alias="x-flychain-tags"),
    ) -> Response:
        run_store: TrainingRunStore = app.state.training_run_store
        run = run_store.load(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no such training run: {run_id}")
        if body.stream:
            raise HTTPException(status_code=400, detail="streaming is not supported here")
        project_id, _capability_ids, tags = _extract_headers(
            x_flychain_project=x_flychain_project,
            x_flychain_capabilities=None,
            x_flychain_tags=x_flychain_tags,
            default_project_id=app.state.settings.default_project_id,
        )
        served = await serve_run_adapter_chat(
            run=run,
            body=body,
            project_id=project_id,
            tags=tags,
        )
        return _json_response(
            served["payload"],
            status_code=served["raw_status"] if served["raw_status"] < 400 else 502,
            extra_headers={
                "x-flychain-trace-id": served["trace_id"],
                "x-flychain-provider": served["provider"],
                "x-flychain-model": served["model"],
                "x-flychain-active-adapter-run-id": served["adapter_run_id"],
                "x-flychain-active-adapter-capability-id": served["adapter_capability_id"],
            },
        )

    @app.post("/v1/messages")
    async def anthropic_messages(
        req: Request,
        body: AnthropicMessagesRequest,
        x_flychain_project: str | None = Header(default=None, alias="x-flychain-project"),
        x_flychain_capabilities: str | None = Header(default=None, alias="x-flychain-capabilities"),
        x_flychain_tags: str | None = Header(default=None, alias="x-flychain-tags"),
    ) -> Response:
        if body.stream:
            raise HTTPException(
                status_code=400,
                detail="streaming is not yet supported on the FlyChain gateway",
            )

        settings: Settings = app.state.settings
        router: ProviderRouter = app.state.router
        store: TraceStore = app.state.trace_store
        registry: ModelRegistry = app.state.registry

        project_id, capability_ids, tags = _extract_headers(
            x_flychain_project=x_flychain_project,
            x_flychain_capabilities=x_flychain_capabilities,
            x_flychain_tags=x_flychain_tags,
            default_project_id=settings.default_project_id,
        )

        try:
            resolved = router.resolve_messages(body.model)
        except (ModelNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        payload = body.model_dump(exclude_none=True)
        payload["model"] = resolved.model_id

        trace_id = _new_id("trace")
        tracer = get_tracer()
        t0 = time.perf_counter()
        status = "ok"
        error = ""

        with tracer.start_as_current_span(f"messages/{resolved.provider_name}") as span:
            span.set_attribute("flychain.trace_id", trace_id)
            span.set_attribute("flychain.project_id", project_id)
            try:
                result = await resolved.adapter.messages(
                    model=resolved.model_id,
                    body=payload,
                    api_key=resolved.api_key,
                )
            except Exception as exc:  # pragma: no cover - exercised via integration
                span.record_exception(exc)
                latency_ms = int((time.perf_counter() - t0) * 1000)
                await store.insert_trace(
                    TraceRecord(
                        trace_id=trace_id,
                        project_id=project_id,
                        provider=resolved.provider_name,
                        model=resolved.model_id,
                        method="messages",
                        request=payload,
                        response=None,
                        capability_ids=capability_ids,
                        latency_ms=latency_ms,
                        status="error",
                        error=str(exc),
                        tags=tags,
                    )
                )
                raise HTTPException(status_code=502, detail=f"upstream error: {exc}") from exc

            latency_ms = int((time.perf_counter() - t0) * 1000)
            if result.error:
                status = "error"
                error = result.error
                span.set_status(otel_trace.Status(otel_trace.StatusCode.ERROR, error))

            cost_usd = registry.cost_usd(
                resolved.model_id, result.prompt_tokens, result.completion_tokens
            )
            span.set_attributes(
                make_llm_attributes(
                    provider=resolved.provider_name,
                    model=resolved.model_id,
                    method="messages",
                    request_payload=payload,
                    response_payload=result.payload,
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    total_tokens=result.total_tokens,
                    latency_ms=latency_ms,
                    project_id=project_id,
                )
            )

            await store.insert_trace(
                TraceRecord(
                    trace_id=trace_id,
                    project_id=project_id,
                    provider=resolved.provider_name,
                    model=resolved.model_id,
                    method="messages",
                    request=payload,
                    response=result.payload,
                    capability_ids=capability_ids,
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    total_tokens=result.total_tokens,
                    cost_usd=cost_usd,
                    latency_ms=latency_ms,
                    status=status,
                    error=error,
                    tags=tags,
                )
            )
            await maybe_enqueue_auto_eval(
                trace_id=trace_id,
                project_id=project_id,
                input_text=_chat_input_text(body.messages),
                output_text=_messages_output_text(result.payload),
                tags=tags,
                capability_ids=capability_ids,
            )

        response_body = dict(result.payload)
        response_body.setdefault("id", trace_id)
        return _json_response(
            response_body,
            status_code=result.raw_status if result.raw_status < 400 else 502,
            extra_headers={"x-flychain-trace-id": trace_id},
        )

    @app.post("/v1/feedback", response_model=FeedbackAccepted)
    async def feedback(payload: FeedbackRequest) -> FeedbackAccepted:
        settings: Settings = app.state.settings
        store: TraceStore = app.state.trace_store

        feedback_id = _new_id("fb")
        thumb = payload.thumb or "none"
        score = int(payload.score) if payload.score is not None else 0
        await store.insert_feedback(
            feedback_id=feedback_id,
            trace_id=payload.trace_id,
            project_id=payload.project_id or settings.default_project_id,
            thumb=thumb,
            score=score,
            comment=payload.comment or "",
            corrected_response=payload.corrected_response or "",
            correction_source="human",
        )
        if payload.corrected_response:
            trace_rows = [row for row in list_all_traces() if row["trace_id"] == payload.trace_id]
            capability_ids: set[str] = set()
            for row in trace_rows:
                capability_ids.update(str(item) for item in row.get("capability_ids", []) or [])
            capability_ids.update(
                str(row["capability_id"])
                for row in store.list_eval_scores(trace_id=payload.trace_id)
            )
            await maybe_run_enabled_autopilot(capability_ids, trigger="correction_added")
        return FeedbackAccepted(feedback_id=feedback_id, trace_id=payload.trace_id)

    @app.get("/v1/capabilities/templates")
    def capabilities_templates() -> dict[str, Any]:
        """Return the shipped capability template library (5 in v1)."""
        templates = list_templates()
        return {"templates": [t.model_dump(mode="json") for t in templates]}

    @app.get("/v1/capabilities")
    def capabilities_list() -> dict[str, Any]:
        store: CapabilityStore = app.state.capability_store
        specs = store.list()
        return {"capabilities": [s.model_dump(mode="json") for s in specs]}

    @app.get("/v1/capabilities/{capability_id}")
    def capabilities_get(capability_id: str) -> dict[str, Any]:
        store: CapabilityStore = app.state.capability_store
        try:
            spec = store.get(capability_id)
        except CapabilityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"no such capability: {exc}") from exc
        return spec.model_dump(mode="json")

    @app.post("/v1/capabilities/from-template", status_code=201)
    def capabilities_from_template(body: CreateFromTemplate) -> dict[str, Any]:
        store: CapabilityStore = app.state.capability_store
        try:
            template_spec = template_by_id(body.template_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail=f"no such template: {body.template_id}"
            ) from exc

        new_id = slugify(body.id or template_spec.id)
        spec = template_spec.model_copy(
            update={
                "id": new_id,
                "name": body.name or template_spec.name,
            }
        )
        try:
            store.create(spec, overwrite=body.overwrite)
        except CapabilityExistsError as exc:
            raise HTTPException(
                status_code=409, detail=f"capability already exists: {exc}"
            ) from exc
        return spec.model_dump(mode="json")

    @app.post("/v1/capabilities", status_code=201)
    def capabilities_create(body: CapabilitySpec) -> dict[str, Any]:
        store: CapabilityStore = app.state.capability_store
        try:
            store.create(body)
        except CapabilityExistsError as exc:
            raise HTTPException(
                status_code=409, detail=f"capability already exists: {exc}"
            ) from exc
        return body.model_dump(mode="json")

    @app.delete("/v1/capabilities/{capability_id}", status_code=204)
    def capabilities_delete(capability_id: str) -> Response:
        store: CapabilityStore = app.state.capability_store
        try:
            store.delete(capability_id)
        except CapabilityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"no such capability: {exc}") from exc
        return Response(status_code=204)

    @app.post("/v1/capabilities/compiler/questions")
    async def capabilities_compiler_questions(body: CompilerInput) -> dict[str, Any]:
        """Phase 3: NL description -> interview questions.

        This lives on the gateway (rather than the orchestrator) so the
        dashboard can stream it from a single origin.
        """
        runtime = local_settings()
        compiler = CapabilityCompiler(llm=judge_client(runtime))
        questions = await compiler.propose_questions(body.description)
        return {
            "questions": [{"id": q.id, "question": q.question} for q in questions],
            "provider": compiler.llm.provider,
            "model": compiler.llm.model,
        }

    @app.post("/v1/capabilities/compiler/compile")
    async def capabilities_compiler_compile(body: CompilerCompile) -> dict[str, Any]:
        """Phase 3: NL description (+ answers) -> CapabilitySpec.

        The returned spec is not persisted - the client reviews it and then
        POSTs to /v1/capabilities if they want to save it.
        """
        runtime = local_settings()
        compiler = CapabilityCompiler(llm=judge_client(runtime))
        spec = await compiler.compile(body.description, body.answers or {})
        return {
            "spec": spec.model_dump(mode="json"),
            "provider": compiler.llm.provider,
            "model": compiler.llm.model,
        }

    @app.post("/v1/eval")
    async def eval_trace(body: EvalRequest) -> dict[str, Any]:
        """Run auto-eval against a trace for one or all tracked capabilities.

        Callers supply the trace contents (``input``, ``output``, optional
        ``context`` and ``tags``). If ``capability_ids`` is omitted, every
        tracked capability whose slice rules match the trace is evaluated.
        Scores are persisted to the trace store (ClickHouse or the in-memory
        buffer) and returned to the caller.
        """
        capability_store: CapabilityStore = app.state.capability_store
        trace_store: TraceStore = app.state.trace_store

        if body.capability_ids:
            specs: list[CapabilitySpec] = []
            for cap_id in body.capability_ids:
                try:
                    specs.append(capability_store.get(cap_id))
                except CapabilityNotFoundError as exc:
                    raise HTTPException(
                        status_code=404, detail=f"no such capability: {exc}"
                    ) from exc
        else:
            specs = capability_store.list()

        runtime = local_settings()
        engine = EvalEngine(llm=judge_client(runtime))
        trace = TraceData(
            trace_id=body.trace_id,
            project_id=body.project_id or "default",
            input=body.input,
            output=body.output,
            context=body.context or "",
            tags=body.tags or {},
        )

        all_scores = []
        per_capability: dict[str, dict[str, Any]] = {}
        for spec in specs:
            scores = await engine.evaluate_trace(trace, spec)
            if not scores:
                continue
            all_scores.extend(scores)
            per_capability[spec.id] = {
                "aggregate_score": aggregate_score(scores, spec),
                "scores": [s.as_dict() for s in scores],
            }

        if all_scores:
            await trace_store.insert_eval_scores([s.as_dict() for s in all_scores])

        failed_capability_ids = {score.capability_id for score in all_scores if not score.passed}
        auto_clusters = await maybe_auto_cluster_failures(
            specs=specs,
            failed_capability_ids=failed_capability_ids,
        )
        autopilot = await maybe_run_enabled_autopilot(
            failed_capability_ids, trigger="eval_persisted"
        )

        return {
            "trace_id": body.trace_id,
            "evaluated_capabilities": list(per_capability.keys()),
            "per_capability": per_capability,
            "auto_clusters": auto_clusters,
            "autopilot": autopilot,
        }

    @app.get("/debug/traces")
    def debug_traces() -> list[dict[str, Any]]:
        store: TraceStore = app.state.trace_store
        traces, _total = store.list_traces(limit=500, offset=0)
        return traces

    @app.get("/debug/feedback")
    def debug_feedback() -> list[dict[str, Any]]:
        store: TraceStore = app.state.trace_store
        return store.list_feedback()

    @app.get("/debug/eval-scores")
    def debug_eval_scores() -> list[dict[str, Any]]:
        store: TraceStore = app.state.trace_store
        return store.list_eval_scores()

    @app.get("/v1/traces")
    def traces_list(
        project_id: str | None = None,
        capability_id: str | None = None,
        status: str | None = None,
        provider: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        store: TraceStore = app.state.trace_store
        traces, total = store.list_traces(
            project_id=project_id,
            capability_id=capability_id,
            status=status,
            provider=provider,
            limit=limit,
            offset=offset,
        )
        return {"traces": traces, "total": total, "limit": limit, "offset": offset}

    @app.get("/v1/traces/{trace_id}/evals")
    def trace_evals(trace_id: str, capability_id: str | None = None) -> dict[str, Any]:
        return eval_score_summary(trace_id=trace_id, capability_id=capability_id)

    @app.get("/v1/jobs")
    def jobs_list(limit: int = 100) -> dict[str, Any]:
        job_store: JobStore = app.state.job_store
        return {"jobs": [job.as_dict() for job in job_store.list(limit=limit)]}

    @app.get("/v1/jobs/{job_id}")
    def jobs_get(job_id: str) -> dict[str, Any]:
        job_store: JobStore = app.state.job_store
        job = job_store.load(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"no such job: {job_id}")
        return job.as_dict()

    @app.post("/v1/jobs/{job_id}/retry", status_code=202)
    async def jobs_retry(job_id: str) -> dict[str, Any]:
        job_store: JobStore = app.state.job_store
        job = job_store.load(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"no such job: {job_id}")
        if job.status not in {"failed", "timed_out"}:
            raise HTTPException(status_code=409, detail=f"job is not retryable: {job.status}")
        if job.retry_count >= job.max_retries:
            raise HTTPException(status_code=409, detail="job has no retries remaining")
        if not job.retry_payload:
            raise HTTPException(status_code=409, detail="job has no retry payload")
        function = str(job.retry_payload.get("function") or "")
        kwargs = dict(job.retry_payload.get("kwargs") or {})
        if not function:
            raise HTTPException(status_code=409, detail="job retry payload missing function")
        kwargs["job_id"] = job.id
        queue = require_job_queue()
        retried = job_store.queue_retry(job.id)
        assert retried is not None
        try:
            await queue.enqueue_job(function, **kwargs)
        except Exception as exc:
            job_store.fail(job.id, error=f"queue enqueue failed: {exc}")
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return retried.as_dict()

    @app.get("/v1/settings")
    def settings_get() -> dict[str, Any]:
        runtime = local_settings()
        settings: Settings = app.state.settings
        return {
            "settings": runtime.model_dump(mode="json"),
            "openai_configured": bool(settings.openai_api_key or os.environ.get("OPENAI_API_KEY")),
            "anthropic_configured": bool(
                settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
            ),
            "runtime": {
                "env": settings.env,
                "ollama_url": settings.ollama_url,
                "mlx_server_url": settings.mlx_server_url,
                "clickhouse_url": settings.clickhouse_url,
                "postgres_url": settings.postgres_url,
                "redis_url": settings.redis_url,
                "data_dir": str(default_data_dir()),
                "health": _runtime_health(app, settings),
            },
        }

    @app.put("/v1/settings")
    def settings_put(body: UpdateSettingsRequest) -> dict[str, Any]:
        store: SettingsStore = app.state.local_settings_store
        current = store.load()
        settings = store.save(current.model_copy(update=body.model_dump(exclude_none=True)))
        return {
            "settings": settings.model_dump(mode="json"),
            "openai_configured": bool(os.environ.get("OPENAI_API_KEY")),
            "anthropic_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "runtime": {
                "env": app.state.settings.env,
                "ollama_url": app.state.settings.ollama_url,
                "mlx_server_url": app.state.settings.mlx_server_url,
                "clickhouse_url": app.state.settings.clickhouse_url,
                "postgres_url": app.state.settings.postgres_url,
                "redis_url": app.state.settings.redis_url,
                "data_dir": str(default_data_dir()),
                "health": _runtime_health(app, app.state.settings),
            },
        }

    @app.get("/v1/capabilities/{capability_id}/failures")
    def capabilities_failures(capability_id: str) -> dict[str, Any]:
        return {
            "capability_id": capability_id,
            "failures": derive_failures(capability_id),
        }

    @app.post("/v1/capabilities/{capability_id}/failures/{trace_id}/review")
    def capabilities_failure_review(
        capability_id: str,
        trace_id: str,
        body: FailureReviewRequest,
    ) -> dict[str, Any]:
        capability_store: CapabilityStore = app.state.capability_store
        if not capability_store.exists(capability_id):
            raise HTTPException(status_code=404, detail="no such capability")
        failure_ids = {row["trace_id"] for row in derive_failures(capability_id)}
        if trace_id not in failure_ids:
            raise HTTPException(status_code=404, detail=f"no such failure: {trace_id}")
        review_store: FailureReviewStore = app.state.failure_review_store
        review = review_store.save(
            capability_id=capability_id,
            trace_id=trace_id,
            status=body.status,
            note=body.note or "",
            updated_at=_now_iso(),
        )
        return review.as_dict()

    @app.get("/v1/capabilities/{capability_id}/flywheel")
    def capability_flywheel(capability_id: str) -> dict[str, Any]:
        capability_store: CapabilityStore = app.state.capability_store
        cluster_store: ClusterStore = app.state.cluster_store
        dataset_store: DatasetStore = app.state.dataset_store
        run_store: TrainingRunStore = app.state.training_run_store
        adapter_store: AdapterPointerStore = app.state.adapter_pointer_store
        job_store: JobStore = app.state.job_store
        trace_store: TraceStore = app.state.trace_store

        try:
            spec = capability_store.get(capability_id)
        except CapabilityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"no such capability: {exc}") from exc

        eval_rows = trace_store.list_eval_scores(capability_id=capability_id)
        eval_trace_ids = {row["trace_id"] for row in eval_rows}
        all_traces = list_all_traces()
        traces = [
            row
            for row in all_traces
            if capability_id in (row.get("capability_ids") or [])
            or row["trace_id"] in eval_trace_ids
        ]
        traces.sort(key=lambda row: row.get("ts", ""), reverse=True)

        failures = derive_failures(capability_id)
        failure_by_trace = {row["trace_id"]: row for row in failures}
        clusters_data = cluster_store.load(capability_id) or {
            "capability_id": capability_id,
            "clusters": [],
            "noise_trace_ids": [],
        }
        datasets = dataset_store.list_for_capability(capability_id)
        runs = run_store.list_for_capability(capability_id)
        run_ids = {run.id for run in runs}
        active_adapter = adapter_store.get(capability_id)
        active_run_id = active_adapter.get("active_run_id") if active_adapter else None
        jobs = [
            job.as_dict()
            for job in job_store.list(limit=200)
            if job.capability_id == capability_id or job.run_id in run_ids
        ]

        cluster_rows = enrich_clusters(
            clusters_data.get("clusters", []),
            datasets=datasets,
            failure_by_trace=failure_by_trace,
        )
        dataset_rows = enrich_datasets(
            datasets,
            runs=runs,
            clusters=clusters_data.get("clusters", []),
            failure_by_trace=failure_by_trace,
        )
        run_rows = [
            enrich_training_run(run, active_run_id=active_run_id)
            for run in sorted(runs, key=lambda item: item.updated_at, reverse=True)
        ]
        latest_validation = latest_served_validation(runs)
        last_adapted = last_adapted_chat(traces)
        before_after = latest_before_after(runs, active_run_id=active_run_id)

        unresolved_failures = [
            failure
            for failure in failures
            if failure["correction_status"] != "corrected"
            and failure["review_status"] != "not_useful"
        ]
        corrected_failures = [
            failure for failure in failures if failure["correction_status"] == "corrected"
        ]
        promoted_runs = [run for run in runs if run.status == "promoted"]
        served_count = 1 if last_adapted else 0

        summary = {
            "total_traces": len({row["trace_id"] for row in traces}),
            "evaluated_traces": len(eval_trace_ids),
            "failing_traces": len(failures),
            "unresolved_failures": len(unresolved_failures),
            "clusters": len(cluster_rows),
            "datasets": len(dataset_rows),
            "training_runs": len(runs),
            "latest_served_validation": latest_validation,
            "active_adapter": active_adapter,
            "last_adapted_chat": last_adapted,
        }
        timeline = [
            timeline_step("capture", "Capture traces", len(traces), traces, "#traces"),
            timeline_step("evaluate", "Evaluate", len(eval_trace_ids), eval_rows, "#traces"),
            timeline_step("fail", "Detect failures", len(failures), failures, "#failures"),
            timeline_step(
                "correct",
                "Collect corrections",
                len(corrected_failures),
                corrected_failures,
                "#failures",
            ),
            timeline_step("cluster", "Cluster", len(cluster_rows), cluster_rows, "#clusters"),
            timeline_step(
                "dataset",
                "Synthesize dataset",
                len(dataset_rows),
                dataset_rows,
                "#datasets",
            ),
            timeline_step("train", "Train", len(runs), run_rows, "#runs"),
            timeline_step(
                "validate",
                "Validate served adapter",
                1 if latest_validation else 0,
                [latest_validation] if latest_validation else [],
                "#runs",
            ),
            timeline_step("promote", "Promote", len(promoted_runs), run_rows, "#runs"),
            timeline_step("serve", "Serve active adapter", served_count, traces, "#before-after"),
        ]

        return {
            "capability": spec.model_dump(mode="json"),
            "capability_id": capability_id,
            "summary": summary,
            "timeline": timeline,
            "traces": traces,
            "failures": failures,
            "clusters": cluster_rows,
            "datasets": dataset_rows,
            "training_runs": run_rows,
            "jobs": jobs,
            "active_adapter": {"capability_id": capability_id, "active": active_adapter},
            "before_after": before_after,
        }

    @app.get("/v1/capabilities/{capability_id}/guided-actions")
    def capability_guided_actions(capability_id: str) -> dict[str, Any]:
        return build_guided_actions(capability_id)

    @app.post("/v1/capabilities/{capability_id}/guided-actions/{action_id:path}/execute")
    async def capability_guided_action_execute(
        capability_id: str,
        action_id: str,
        body: GuidedActionExecuteRequest,
    ) -> dict[str, Any]:
        action_type, target_id = parse_guided_action_id(action_id)
        actions_body = build_guided_actions(capability_id)
        action = next(
            (item for item in actions_body["actions"] if item["id"] == action_id),
            None,
        )
        if action is None:
            raise HTTPException(status_code=404, detail=f"no such guided action: {action_id}")
        if action["status"] != "available":
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"guided action is {action['status']}",
                    "blocked_reasons": action.get("blocked_reasons", []),
                },
            )
        if action.get("requires_approval") and not body.approved:
            raise HTTPException(
                status_code=409,
                detail="explicit approval is required for this guided action",
            )

        if action_type == "create_dataset":
            capability_store: CapabilityStore = app.state.capability_store
            dataset_store: DatasetStore = app.state.dataset_store
            try:
                spec = capability_store.get(capability_id)
            except CapabilityNotFoundError as exc:
                raise HTTPException(
                    status_code=404, detail=f"no such capability: {exc}"
                ) from exc
            cluster = resolve_cluster(capability_id, target_id)
            included, skipped = guided_cluster_failures(capability_id, cluster)
            included_trace_ids = [row["trace_id"] for row in included]
            cluster_for_dataset = Cluster(
                id=cluster.id,
                capability_id=cluster.capability_id,
                label=cluster.label,
                size=len(included_trace_ids),
                trace_ids=included_trace_ids,
            )
            failures = [failed_trace_from_row(row) for row in included]
            job = create_job(
                job_type="dataset_synthesis",
                capability_id=capability_id,
                trace_ids=included_trace_ids,
                cluster_id=cluster.id,
                max_retries=1,
            )
            started_job = app.state.job_store.start(job.id) or job
            try:
                rows = await wait_for_job_timeout(
                    started_job,
                    synthesize_sft_dataset(
                        capability=spec,
                        cluster=cluster_for_dataset,
                        failures=failures,
                        llm=None,
                        generate_missing=False,
                    ),
                )
                dataset_id = f"ds_{ULID()}"
                out_dir = default_data_dir() / "datasets" / capability_id
                out_path = out_dir / f"{dataset_id}.jsonl"
                write_jsonl(out_path, rows)
                record = SynthesizedDataset(
                    id=dataset_id,
                    capability_id=capability_id,
                    cluster_id=cluster.id,
                    method="sft",
                    path=str(out_path),
                    row_count=len(rows),
                )
                dataset_store.record(record)
                started_job.dataset_id = record.id
                app.state.job_store.save(started_job)
                app.state.job_store.succeed(job.id)
                return {
                    "capability_id": capability_id,
                    "action": action,
                    "result": {
                        "dataset_id": record.id,
                        "cluster_id": cluster.id,
                        "method": record.method,
                        "path": record.path,
                        "row_count": record.row_count,
                        "included_trace_ids": included_trace_ids,
                        "skipped": skipped,
                        "job_id": job.id,
                    },
                }
            except TimeoutError as exc:
                app.state.job_store.timeout(
                    job.id,
                    error=f"dataset synthesis job timed out after {started_job.timeout_seconds}s",
                )
                raise HTTPException(
                    status_code=504, detail="dataset synthesis job timed out"
                ) from exc
            except Exception as exc:
                app.state.job_store.fail(job.id, error=str(exc))
                raise

        if action_type == "start_training":
            preview = dict(action.get("preview") or {})
            run = await queue_training_run(
                CreateTrainingRun(
                    capability_id=capability_id,
                    recipe_id=str(preview.get("recipe_id") or ""),
                    dataset_id=target_id,
                    allow_backend_fallback=False,
                )
            )
            return {
                "capability_id": capability_id,
                "action": action,
                "result": {
                    "run_id": run.id,
                    "status": run.status,
                    "dataset_id": run.dataset_id,
                    "recipe_id": run.recipe_id,
                    "allow_backend_fallback": run.allow_backend_fallback,
                },
            }

        if action_type == "run_served_validation":
            validation_run_store: TrainingRunStore = app.state.training_run_store
            validation_run = validation_run_store.load(target_id)
            if validation_run is None or validation_run.capability_id != capability_id:
                raise HTTPException(status_code=404, detail=f"no such run: {target_id}")
            resolve_run_mlx_adapter(validation_run)
            replay_set = managed_replay_set_for_run(validation_run)
            queued = await queue_served_validation(validation_run.id, replay_set.id)
            validation = queued.served_validation or {}
            return {
                "capability_id": capability_id,
                "action": action,
                "result": {
                    "run_id": queued.id,
                    "status": queued.status,
                    "replay_set_id": replay_set.id,
                    "job_id": validation.get("job_id"),
                    "managed_replay_name": replay_set.name,
                    "row_count": len(replay_set.rows),
                },
            }

        if action_type == "promote_adapter":
            promotion_run_store: TrainingRunStore = app.state.training_run_store
            adapter_store: AdapterPointerStore = app.state.adapter_pointer_store
            autopilot_store: AutopilotStore = app.state.autopilot_store
            promotion_run = promotion_run_store.load(target_id)
            if promotion_run is None:
                raise HTTPException(status_code=404, detail=f"no such run: {target_id}")
            if promotion_run.capability_id != capability_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"run {target_id} belongs to capability {promotion_run.capability_id}",
                )
            if promotion_run.status not in {"validated", "promoted"}:
                raise HTTPException(
                    status_code=409,
                    detail=f"run not in promotable state: {promotion_run.status}",
                )
            if promotion_run.artifact is None:
                raise HTTPException(status_code=409, detail="run has no artifact")
            if not run_requires_served_validation(promotion_run):
                raise HTTPException(
                    status_code=409,
                    detail="run has no real served adapter artifact",
                )
            if not run_has_served_validation(promotion_run):
                raise HTTPException(
                    status_code=409,
                    detail=served_validation_error_detail(promotion_run)
                    or "run cannot be promoted until served validation passes",
                )
            previous = adapter_store.get(capability_id)
            adapter_store.set_active(
                capability_id,
                run_id=promotion_run.id,
                adapter_dir=str(promotion_run.artifact.get("adapter_dir", "")),
                baseline=promotion_run.baseline,
                candidate=promotion_run.candidate,
            )
            autopilot_store.record_adapter_history(
                capability_id,
                previous=previous,
                current=adapter_store.get(capability_id),
                reason="guided promotion",
            )
            promotion_run.status = "promoted"
            promotion_run.updated_at = _now_iso()
            promotion_run_store.save(promotion_run)
            proof = await post_activation_check(capability_id, promotion_run)
            return {
                "capability_id": capability_id,
                "action": action,
                "result": {
                    "active_run_id": promotion_run.id,
                    "post_activation_check": proof,
                },
            }

        raise HTTPException(status_code=400, detail=f"unsupported guided action: {action_type}")

    @app.get("/v1/capabilities/{capability_id}/autopilot-policy")
    def capability_autopilot_policy(capability_id: str) -> dict[str, Any]:
        capability_store: CapabilityStore = app.state.capability_store
        if not capability_store.exists(capability_id):
            raise HTTPException(status_code=404, detail="no such capability")
        policy = autopilot_policy(capability_id)
        return {"capability_id": capability_id, "policy": policy.as_dict()}

    @app.put("/v1/capabilities/{capability_id}/autopilot-policy")
    def capability_autopilot_policy_update(
        capability_id: str, body: AutopilotPolicyUpdate
    ) -> dict[str, Any]:
        capability_store: CapabilityStore = app.state.capability_store
        if not capability_store.exists(capability_id):
            raise HTTPException(status_code=404, detail="no such capability")
        store: AutopilotStore = app.state.autopilot_store
        policy = store.save_policy(
            capability_id,
            threshold=int(local_settings().min_cluster_size),
            patch=body.model_dump(exclude_none=True),
        )
        return {"capability_id": capability_id, "policy": policy.as_dict()}

    @app.get("/v1/capabilities/{capability_id}/autopilot/audit")
    def capability_autopilot_audit(capability_id: str) -> dict[str, Any]:
        capability_store: CapabilityStore = app.state.capability_store
        if not capability_store.exists(capability_id):
            raise HTTPException(status_code=404, detail="no such capability")
        store: AutopilotStore = app.state.autopilot_store
        return {"capability_id": capability_id, "audit": store.list_audit(capability_id)}

    @app.get("/v1/capabilities/{capability_id}/autopilot")
    def capability_autopilot_status(capability_id: str) -> dict[str, Any]:
        capability_store: CapabilityStore = app.state.capability_store
        if not capability_store.exists(capability_id):
            raise HTTPException(status_code=404, detail="no such capability")
        store: AutopilotStore = app.state.autopilot_store
        policy = autopilot_policy(capability_id)
        audit = store.list_audit(capability_id)
        pending = next(
            (
                row
                for row in audit
                if row.get("approval_status") == "pending"
                and row.get("outcome") == "approval_required"
            ),
            None,
        )
        return {
            "capability_id": capability_id,
            "policy": policy.as_dict(),
            "readiness": autopilot_counts(capability_id, policy=policy),
            "latest_decision": audit[0] if audit else None,
            "pending_approval": pending,
            "audit": audit,
        }

    @app.post("/v1/capabilities/{capability_id}/autopilot/run")
    async def capability_autopilot_run(
        capability_id: str, body: AutopilotRunRequest
    ) -> dict[str, Any]:
        return await run_autopilot(capability_id, trigger=body.trigger or "manual")

    @app.post("/v1/capabilities/{capability_id}/autopilot/approvals/{decision_id}")
    def capability_autopilot_approval(
        capability_id: str,
        decision_id: str,
        body: AutopilotApprovalRequest,
    ) -> dict[str, Any]:
        store: AutopilotStore = app.state.autopilot_store
        run_store: TrainingRunStore = app.state.training_run_store
        decision = store.load_decision(capability_id, decision_id)
        if decision is None:
            raise HTTPException(status_code=404, detail="no such autopilot decision")
        if decision.approval_status != "pending":
            raise HTTPException(status_code=409, detail="decision is not pending approval")
        if not body.approved:
            decision.approval_status = "rejected"
            decision.approval_note = body.note or ""
            decision.outcome = "rejected"
            store.save_decision(decision)
            return {"capability_id": capability_id, "decision": decision.as_dict()}
        if decision.action != "promote_adapter" or not decision.target_id:
            raise HTTPException(status_code=409, detail="decision cannot be approved")
        run = run_store.load(decision.target_id)
        if run is None or run.capability_id != capability_id:
            raise HTTPException(status_code=404, detail="no such run")
        if not run_has_served_validation(run):
            raise HTTPException(
                status_code=409,
                detail=served_validation_error_detail(run) or "served validation has not passed",
            )
        policy = autopilot_policy(capability_id)
        cooldown_remaining = promotion_cooldown_remaining(capability_id, policy)
        if cooldown_remaining > 0:
            decision.outcome = "blocked"
            decision.approval_status = "blocked"
            decision.reasons.append(f"promotion cooldown active for {cooldown_remaining}s")
            store.save_decision(decision)
            raise HTTPException(
                status_code=409,
                detail=f"promotion cooldown active for {cooldown_remaining}s",
            )
        result = promote_run_for_autopilot(
            capability_id,
            run,
            reason="autopilot approval",
            decision_id=decision.id,
        )
        decision.approval_status = "approved"
        decision.approval_note = body.note or ""
        decision.approved_at = _now_iso()
        decision.outcome = "complete"
        decision.result = result
        store.save_decision(decision)
        return {**result, "decision": decision.as_dict()}

    @app.post("/v1/capabilities/{capability_id}/rollback")
    def capability_rollback(capability_id: str, body: RollbackRequest) -> dict[str, Any]:
        capability_store: CapabilityStore = app.state.capability_store
        if not capability_store.exists(capability_id):
            raise HTTPException(status_code=404, detail="no such capability")
        adapter_store: AdapterPointerStore = app.state.adapter_pointer_store
        store: AutopilotStore = app.state.autopilot_store
        policy = autopilot_policy(capability_id)
        mode = body.mode or policy.rollback_mode
        previous_active = adapter_store.get(capability_id)
        if previous_active is None:
            decision = store.append_decision(
                capability_id,
                trigger="rollback",
                policy_version=policy.version,
                action="rollback",
                outcome="skipped",
                reasons=["no active adapter to roll back"],
                input_counts=autopilot_counts(capability_id, policy=policy),
                result={"mode": mode, "reason": body.reason},
            )
            return {
                "capability_id": capability_id,
                "status": "skipped",
                "decision": decision.as_dict(),
            }
        if mode == "restore_previous":
            restore = store.previous_adapter(capability_id)
            if restore is None:
                decision = store.append_decision(
                    capability_id,
                    trigger="rollback",
                    policy_version=policy.version,
                    action="rollback",
                    outcome="blocked",
                    reasons=["no previous active adapter to restore"],
                    input_counts=autopilot_counts(capability_id, policy=policy),
                    result={"mode": mode, "reason": body.reason},
                )
                return {
                    "capability_id": capability_id,
                    "status": "blocked",
                    "decision": decision.as_dict(),
                }
            adapter_store._path(capability_id).write_text(json.dumps(restore, indent=2))  # noqa: SLF001
            current = adapter_store.get(capability_id)
        else:
            adapter_store.clear(capability_id)
            current = None
        history = store.record_adapter_history(
            capability_id,
            previous=previous_active,
            current=current,
            reason=body.reason,
        )
        decision = store.append_decision(
            capability_id,
            trigger="rollback",
            policy_version=policy.version,
            action="rollback",
            outcome="complete",
            input_counts=autopilot_counts(capability_id, policy=policy),
            result={"mode": mode, "reason": body.reason, "history_id": history["id"]},
        )
        return {
            "capability_id": capability_id,
            "status": "rolled_back",
            "active": current,
            "decision": decision.as_dict(),
        }

    @app.post("/v1/capabilities/{capability_id}/cluster-run")
    async def capabilities_cluster_run(
        capability_id: str, body: ClusterRunRequest
    ) -> dict[str, Any]:
        """Embed the supplied failing traces and cluster them with HDBSCAN.

        Callers pass the failing traces inline - Phase 5 does not yet query
        ClickHouse directly. The cluster result is persisted and returned.
        """
        capability_store: CapabilityStore = app.state.capability_store
        cluster_store: ClusterStore = app.state.cluster_store

        try:
            spec = capability_store.get(capability_id)
        except CapabilityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"no such capability: {exc}") from exc

        if body.failures is not None:
            failures = [
                FailedTrace(
                    trace_id=ft.trace_id,
                    project_id=ft.project_id or "default",
                    input=ft.input,
                    output=ft.output,
                    context=ft.context or "",
                    corrected_response=ft.corrected_response,
                    tags=dict(ft.tags or {}),
                )
                for ft in body.failures
            ]
        elif body.failure_ids:
            failures = resolve_failed_traces(capability_id, body.failure_ids)
        else:
            raise HTTPException(status_code=400, detail="provide failures or failure_ids")

        job = create_job(
            job_type="cluster",
            capability_id=capability_id,
            trace_ids=[failure.trace_id for failure in failures],
            max_retries=1,
        )
        started_job = app.state.job_store.start(job.id) or job
        runtime = local_settings()
        try:
            result = await wait_for_job_timeout(
                started_job,
                cluster_failures(
                    capability=spec,
                    failures=failures,
                    embedder=auto_embedder(ollama_model=runtime.embedding_model),
                    llm=judge_client(runtime) if body.summarize else None,
                    min_cluster_size=body.min_cluster_size,
                    summarize=body.summarize,
                ),
            )
            cluster_store.save(result)
            app.state.job_store.succeed(job.id)
            return result.as_dict()
        except TimeoutError as exc:
            app.state.job_store.timeout(
                job.id, error=f"cluster job timed out after {started_job.timeout_seconds}s"
            )
            raise HTTPException(status_code=504, detail="cluster job timed out") from exc
        except Exception as exc:
            app.state.job_store.fail(job.id, error=str(exc))
            raise

    @app.get("/v1/capabilities/{capability_id}/clusters")
    def capabilities_clusters(capability_id: str) -> dict[str, Any]:
        capability_store: CapabilityStore = app.state.capability_store
        cluster_store: ClusterStore = app.state.cluster_store
        if not capability_store.exists(capability_id):
            raise HTTPException(status_code=404, detail="no such capability")
        data = cluster_store.load(capability_id)
        if data is None:
            return {"capability_id": capability_id, "clusters": [], "noise_trace_ids": []}
        return data

    @app.post("/v1/capabilities/{capability_id}/synthesize-dataset")
    async def capabilities_synthesize_dataset(
        capability_id: str, body: SynthesizeRequest
    ) -> dict[str, Any]:
        """Generate an SFT or DPO JSONL dataset from a cluster's failures."""
        capability_store: CapabilityStore = app.state.capability_store
        dataset_store: DatasetStore = app.state.dataset_store

        try:
            spec = capability_store.get(capability_id)
        except CapabilityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"no such capability: {exc}") from exc

        if body.cluster is not None:
            cluster = Cluster(
                id=body.cluster.id,
                capability_id=body.cluster.capability_id,
                label=body.cluster.label,
                size=body.cluster.size,
                trace_ids=body.cluster.trace_ids,
            )
            if body.failures is None:
                raise HTTPException(
                    status_code=400, detail="provide failures when cluster is inline"
                )
            failures = [
                FailedTrace(
                    trace_id=ft.trace_id,
                    project_id=ft.project_id or "default",
                    input=ft.input,
                    output=ft.output,
                    context=ft.context or "",
                    corrected_response=ft.corrected_response,
                    tags=dict(ft.tags or {}),
                )
                for ft in body.failures
            ]
        elif body.cluster_id:
            cluster = resolve_cluster(capability_id, body.cluster_id)
            failures = resolve_failed_traces(capability_id, cluster.trace_ids)
        else:
            raise HTTPException(status_code=400, detail="provide cluster or cluster_id")

        method = body.method.lower()
        if method not in {"sft", "dpo"}:
            raise HTTPException(status_code=400, detail="method must be sft or dpo")

        job = create_job(
            job_type="dataset_synthesis",
            capability_id=capability_id,
            trace_ids=list(cluster.trace_ids),
            cluster_id=cluster.id,
            max_retries=1,
        )
        started_job = app.state.job_store.start(job.id) or job
        try:
            runtime = local_settings()
            if method == "sft":
                rows_awaitable = synthesize_sft_dataset(
                    capability=spec,
                    cluster=cluster,
                    failures=failures,
                    llm=judge_client(runtime)
                    if body.generate_missing
                    else None,
                    generate_missing=body.generate_missing,
                )
            else:
                rows_awaitable = synthesize_dpo_dataset(
                    capability=spec,
                    cluster=cluster,
                    failures=failures,
                    llm=judge_client(runtime)
                    if body.generate_missing
                    else None,
                    generate_missing=body.generate_missing,
                )
            rows = await wait_for_job_timeout(started_job, rows_awaitable)

            dataset_id = f"ds_{ULID()}"
            out_dir = default_data_dir() / "datasets" / capability_id
            out_path = out_dir / f"{dataset_id}.jsonl"
            write_jsonl(out_path, rows)

            record = SynthesizedDataset(
                id=dataset_id,
                capability_id=capability_id,
                cluster_id=cluster.id,
                method=method,
                path=str(out_path),
                row_count=len(rows),
            )
            dataset_store.record(record)
            started_job.dataset_id = record.id
            app.state.job_store.save(started_job)
            app.state.job_store.succeed(job.id)
            return {
                "id": record.id,
                "capability_id": record.capability_id,
                "cluster_id": record.cluster_id,
                "method": record.method,
                "path": record.path,
                "row_count": record.row_count,
            }
        except TimeoutError as exc:
            app.state.job_store.timeout(
                job.id,
                error=f"dataset synthesis job timed out after {started_job.timeout_seconds}s",
            )
            raise HTTPException(
                status_code=504, detail="dataset synthesis job timed out"
            ) from exc
        except Exception as exc:
            app.state.job_store.fail(job.id, error=str(exc))
            raise

    @app.get("/v1/capabilities/{capability_id}/datasets")
    def capabilities_datasets(capability_id: str) -> dict[str, Any]:
        dataset_store: DatasetStore = app.state.dataset_store
        return {"datasets": dataset_store.list_for_capability(capability_id)}

    @app.get("/v1/recipes")
    def recipes_list() -> dict[str, Any]:
        return {"recipes": [r.model_dump(mode="json") for r in list_recipes()]}

    @app.get("/v1/recipes/{recipe_id}")
    def recipes_get(recipe_id: str) -> dict[str, Any]:
        try:
            return recipe_by_id(recipe_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"no such recipe: {exc}") from exc

    @app.post("/v1/training-runs", status_code=202)
    async def training_runs_create(body: CreateTrainingRun) -> dict[str, Any]:
        """Create a queued training run and hand execution to the orchestrator."""
        run = await queue_training_run(body)
        return _run_to_dict(run)

    @app.get("/v1/training-runs")
    def training_runs_list(capability_id: str | None = None) -> dict[str, Any]:
        run_store: TrainingRunStore = app.state.training_run_store
        runs = run_store.list_for_capability(capability_id) if capability_id else run_store.list()
        return {"runs": [_run_to_dict(r) for r in runs]}

    @app.get("/v1/training-runs/{run_id}")
    def training_runs_get(run_id: str) -> dict[str, Any]:
        run_store: TrainingRunStore = app.state.training_run_store
        run = run_store.load(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no such training run: {run_id}")
        return _run_to_dict(run)

    @app.post("/v1/training-runs/{run_id}/apply-gate", status_code=202)
    async def training_runs_apply_gate(run_id: str, body: ApplyGateRequest) -> dict[str, Any]:
        """Queue gate application for a trained run."""
        run_store: TrainingRunStore = app.state.training_run_store

        run = run_store.load(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no such training run: {run_id}")
        if run.status not in {"trained", "validated", "archived", "promoted"}:
            raise HTTPException(
                status_code=409, detail=f"run not in a gate-eligible state: {run.status}"
            )
        queue = require_job_queue()
        if body.candidate is not None:
            candidate = dict(body.candidate)
            baseline = dict(body.baseline) if body.baseline is not None else None
        elif run.latest_comparison is not None:
            baseline_score = float(run.latest_comparison["baseline"]["aggregate_score"])
            candidate_score = float(run.latest_comparison["candidate"]["aggregate_score"])
            baseline = dict(run.baseline)
            baseline[run.capability_id] = baseline_score
            candidate = {run.capability_id: candidate_score}
        else:
            raise HTTPException(
                status_code=400,
                detail="candidate scores required unless latest comparison is available",
            )

        previous_status = run.status
        previous_baseline = dict(run.baseline)
        previous_candidate = dict(run.candidate)

        run.baseline = dict(baseline if baseline is not None else run.baseline)
        run.candidate = dict(candidate)
        run.status = "gate-queued"
        run.updated_at = _now_iso()
        run_store.save(run)
        job = create_job(
            job_type="promotion_gate",
            capability_id=run.capability_id,
            run_id=run.id,
            max_retries=1,
            retry_payload={
                "function": "apply_promotion_gate",
                "kwargs": {
                    "run_id": run.id,
                    "candidate": candidate,
                    "baseline": baseline,
                },
            },
        )
        job.retry_payload = {
            "function": "apply_promotion_gate",
            "kwargs": {
                "run_id": run.id,
                "candidate": candidate,
                "baseline": baseline,
                "job_id": job.id,
            },
        }
        app.state.job_store.save(job)

        try:
            await queue.enqueue_job(
                "apply_promotion_gate",
                run_id=run.id,
                candidate=candidate,
                baseline=baseline,
                job_id=job.id,
            )
        except Exception as exc:
            app.state.job_store.fail(job.id, error=f"queue enqueue failed: {exc}")
            run.status = previous_status
            run.baseline = previous_baseline
            run.candidate = previous_candidate
            run.error = f"queue enqueue failed: {exc}"
            run.updated_at = _now_iso()
            run_store.save(run)
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        run.error = None
        run_store.save(run)
        return _run_to_dict(run)

    @app.get("/v1/capabilities/{capability_id}/replay-sets")
    def capability_replay_sets(capability_id: str) -> dict[str, Any]:
        replay_store: ReplaySetStore = app.state.replay_set_store
        return {
            "replay_sets": [
                asdict(item) for item in replay_store.list_for_capability(capability_id)
            ]
        }

    @app.post("/v1/capabilities/{capability_id}/replay-sets", status_code=201)
    def capability_replay_sets_create(
        capability_id: str, body: ReplaySetWriteRequest
    ) -> dict[str, Any]:
        capability_store: CapabilityStore = app.state.capability_store
        replay_store: ReplaySetStore = app.state.replay_set_store
        if not capability_store.exists(capability_id):
            raise HTTPException(status_code=404, detail="no such capability")
        record = ReplaySet(
            id=f"replay_{ULID()}",
            capability_id=capability_id,
            name=body.name,
            rows=[row.model_dump(mode="json") for row in body.rows],
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )
        replay_store.save(record)
        return asdict(record)

    @app.put("/v1/capabilities/{capability_id}/replay-sets/{replay_set_id}")
    def capability_replay_sets_update(
        capability_id: str, replay_set_id: str, body: ReplaySetWriteRequest
    ) -> dict[str, Any]:
        replay_store: ReplaySetStore = app.state.replay_set_store
        record = replay_store.load(replay_set_id)
        if record is None or record.capability_id != capability_id:
            raise HTTPException(status_code=404, detail=f"no such replay set: {replay_set_id}")
        record.name = body.name
        record.rows = [row.model_dump(mode="json") for row in body.rows]
        record.updated_at = _now_iso()
        replay_store.save(record)
        return asdict(record)

    async def run_served_validation_now(
        *,
        run_id: str,
        replay_set_id: str,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        run_store: TrainingRunStore = app.state.training_run_store
        replay_store: ReplaySetStore = app.state.replay_set_store
        capability_store: CapabilityStore = app.state.capability_store
        trace_store: TraceStore = app.state.trace_store
        job_store: JobStore = app.state.job_store

        run = run_store.load(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no such training run: {run_id}")
        replay_set = replay_store.load(replay_set_id)
        if replay_set is None or replay_set.capability_id != run.capability_id:
            raise HTTPException(status_code=404, detail=f"no such replay set: {replay_set_id}")
        try:
            spec = capability_store.get(run.capability_id)
        except CapabilityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"no such capability: {exc}") from exc

        if job_id:
            job_store.start(job_id)

        started_at = _now_iso()
        run.status = "validation-running"
        run.error = None
        run.served_validation = {
            "status": "running",
            "replay_set_id": replay_set_id,
            "job_id": job_id,
            "started_at": started_at,
        }
        run.updated_at = started_at
        run_store.save(run)

        validation_trace_ids: list[str] = []
        all_scores = []
        failures: list[dict[str, Any]] = []
        provider = ""
        model = ""
        outputs: list[str] = []
        validation_rows: list[dict[str, Any]] = []
        adapter_run_id = ""
        adapter_capability_id = ""
        routing_mode = ""

        try:
            runtime = local_settings()
            engine = EvalEngine(llm=judge_client(runtime))
            transport = httpx.ASGITransport(app=app)
            for row_dict in replay_set.rows:
                row = ReplayRow.model_validate(row_dict)
                messages: list[ChatMessage] = []
                if row.context:
                    messages.append(ChatMessage(role="system", content=row.context))
                messages.append(ChatMessage(role="user", content=row.input))
                request_body = ChatCompletionRequest(
                    model=str((run.artifact or {}).get("base_model") or ""),
                    messages=messages,
                    stream=False,
                ).model_dump(exclude_none=True)
                headers = {
                    "x-flychain-project": row.project_id or app.state.settings.default_project_id,
                    "x-flychain-capabilities": run.capability_id,
                    "x-flychain-candidate-run-id": run.id,
                }
                if row.tags:
                    headers["x-flychain-tags"] = ",".join(
                        f"{k}={v}" for k, v in row.tags.items()
                    )
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://flychain-internal"
                ) as client:
                    response = await client.post(
                        "/v1/chat/completions",
                        json=request_body,
                        headers=headers,
                    )
                response.raise_for_status()
                served_payload = response.json()
                provider = response.headers.get("x-flychain-provider", "")
                model = response.headers.get("x-flychain-model", "")
                trace_id = response.headers.get("x-flychain-trace-id", "")
                adapter_run_id = response.headers.get("x-flychain-adapter-run-id", "")
                adapter_capability_id = response.headers.get(
                    "x-flychain-adapter-capability-id", ""
                )
                routing_mode = response.headers.get("x-flychain-adapter-routing-mode", "")
                output_text = _chat_output_text(served_payload)
                if trace_id:
                    validation_trace_ids.append(trace_id)
                outputs.append(output_text)

                proof_errors = []
                if not trace_id:
                    proof_errors.append("missing trace id")
                if provider != "local-mlx":
                    proof_errors.append(f"wrong provider {provider or '<missing>'}")
                if adapter_run_id != run.id:
                    proof_errors.append("wrong adapter run id")
                if adapter_capability_id != run.capability_id:
                    proof_errors.append("wrong adapter capability id")
                if routing_mode != "candidate":
                    proof_errors.append("wrong adapter routing mode")

                trace = TraceData(
                    trace_id=trace_id or f"{run.id}:missing-trace",
                    project_id=row.project_id or app.state.settings.default_project_id,
                    input=row.input,
                    output=output_text,
                    context=row.context or "",
                    tags=dict(row.tags or {}),
                )
                scores = await engine.evaluate_trace(trace, spec)
                all_scores.extend(scores)
                row_failed = bool(proof_errors) or any(not score.passed for score in scores)
                validation_rows.append(
                    {
                        "replay_trace_id": row.trace_id,
                        "trace_id": trace.trace_id,
                        "input": row.input,
                        "context": row.context or "",
                        "baseline_output": row.baseline_output,
                        "expected_candidate_output": row.candidate_output,
                        "adapted_output": output_text,
                        "scores": [score.as_dict() for score in scores],
                        "adapter_proof": {
                            "provider": provider,
                            "model": model,
                            "adapter_run_id": adapter_run_id,
                            "adapter_capability_id": adapter_capability_id,
                            "routing_mode": routing_mode,
                        },
                        "proof_errors": proof_errors,
                        "verdict": "failed" if row_failed else "passed",
                    }
                )
                if proof_errors or any(not score.passed for score in scores):
                    failures.append(
                        {
                            "trace_id": trace.trace_id,
                            "replay_trace_id": row.trace_id,
                            "proof_errors": proof_errors,
                            "scores": [score.as_dict() for score in scores if not score.passed],
                        }
                    )

            if all_scores:
                await trace_store.insert_eval_scores([score.as_dict() for score in all_scores])

            aggregate = aggregate_score(all_scores, spec)
            passed = bool(all_scores) and not failures and all(score.passed for score in all_scores)
            finished_at = _now_iso()
            result = {
                "status": "passed" if passed else "failed",
                "replay_set_id": replay_set_id,
                "job_id": job_id,
                "aggregate_score": aggregate,
                "sample_count": len(replay_set.rows),
                "validation_trace_ids": validation_trace_ids,
                "provider": provider,
                "model": model,
                "adapter_run_id": adapter_run_id,
                "adapter_capability_id": adapter_capability_id,
                "routing_mode": routing_mode,
                "outputs": outputs,
                "rows": validation_rows,
                "failures": failures,
                "started_at": started_at,
                "finished_at": finished_at,
            }
            run.served_validation = result
            run.status = "validated" if passed else "validation-failed"
            run.error = None if passed else "served validation failed"
            run.updated_at = finished_at
            run_store.save(run)
            if job_id:
                job_store.succeed(job_id)
            return result
        except Exception as exc:
            finished_at = _now_iso()
            run.served_validation = {
                "status": "failed",
                "replay_set_id": replay_set_id,
                "job_id": job_id,
                "aggregate_score": 0.0,
                "sample_count": len(replay_set.rows),
                "validation_trace_ids": validation_trace_ids,
                "provider": provider,
                "model": model,
                "adapter_run_id": adapter_run_id or run.id,
                "adapter_capability_id": adapter_capability_id or run.capability_id,
                "routing_mode": routing_mode,
                "outputs": outputs,
                "rows": validation_rows,
                "failures": [{"error": str(exc)}],
                "started_at": started_at,
                "finished_at": finished_at,
            }
            run.status = "validation-failed"
            run.error = str(exc)
            run.updated_at = finished_at
            run_store.save(run)
            if job_id:
                job_store.fail(job_id, error=str(exc))
            raise

    @app.post("/v1/training-runs/{run_id}/served-validation", status_code=202)
    async def training_runs_served_validation(
        run_id: str, body: ServedValidationRequest
    ) -> dict[str, Any]:
        run = await queue_served_validation(run_id, body.replay_set_id)
        return _run_to_dict(run)

    @app.post("/internal/training-runs/{run_id}/served-validation/run")
    async def internal_training_runs_served_validation_run(
        run_id: str, body: ServedValidationRunRequest
    ) -> dict[str, Any]:
        return await run_served_validation_now(
            run_id=run_id,
            replay_set_id=body.replay_set_id,
            job_id=body.job_id,
        )

    @app.get("/v1/capabilities/{capability_id}/active-adapter")
    def capability_active_adapter(capability_id: str) -> dict[str, Any]:
        adapter_store: AdapterPointerStore = app.state.adapter_pointer_store
        data = adapter_store.get(capability_id)
        if data is None:
            return {"capability_id": capability_id, "active": None}
        return {"capability_id": capability_id, "active": data}

    @app.post("/v1/capabilities/{capability_id}/active-adapter")
    def capability_activate_run(capability_id: str, body: ActivateRunRequest) -> dict[str, Any]:
        """Manually activate a specific training run as the active adapter.

        The training run must be in ``trained`` or ``promoted`` state. Used
        for the Phase 8 "make active" button after an A/B comparison.
        """
        capability_store: CapabilityStore = app.state.capability_store
        run_store: TrainingRunStore = app.state.training_run_store
        adapter_store: AdapterPointerStore = app.state.adapter_pointer_store
        autopilot_store: AutopilotStore = app.state.autopilot_store

        if not capability_store.exists(capability_id):
            raise HTTPException(status_code=404, detail="no such capability")

        run = run_store.load(body.run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no such run: {body.run_id}")
        if run.capability_id != capability_id:
            raise HTTPException(
                status_code=400,
                detail=f"run {body.run_id} belongs to capability {run.capability_id}",
            )
        if run.status not in {"trained", "validated", "promoted"}:
            raise HTTPException(
                status_code=409,
                detail=f"run not in activatable state: {run.status}",
            )
        if run.artifact is None:
            raise HTTPException(status_code=409, detail="run has no artifact")
        if not run_requires_served_validation(run):
            raise HTTPException(
                status_code=409,
                detail="run has no real served adapter artifact",
            )
        if not run_has_served_validation(run):
            raise HTTPException(
                status_code=409,
                detail=served_validation_error_detail(run)
                or "run cannot be activated until served validation passes",
            )

        previous = adapter_store.get(capability_id)
        adapter_store.set_active(
            capability_id,
            run_id=run.id,
            adapter_dir=str(run.artifact.get("adapter_dir", "")),
            baseline=run.baseline,
            candidate=run.candidate,
        )
        autopilot_store.record_adapter_history(
            capability_id,
            previous=previous,
            current=adapter_store.get(capability_id),
            reason="manual activation",
        )
        run.status = "promoted"
        run.updated_at = _now_iso()
        run_store.save(run)
        return {"capability_id": capability_id, "active_run_id": run.id}

    @app.delete("/v1/capabilities/{capability_id}/active-adapter", status_code=204)
    def capability_deactivate(capability_id: str) -> Response:
        """Clear the active adapter pointer (revert to base model)."""
        adapter_store: AdapterPointerStore = app.state.adapter_pointer_store
        autopilot_store: AutopilotStore = app.state.autopilot_store
        previous = adapter_store.get(capability_id)
        adapter_store.clear(capability_id)
        if previous is not None:
            autopilot_store.record_adapter_history(
                capability_id,
                previous=previous,
                current=None,
                reason="manual clear",
            )
        return Response(status_code=204)

    @app.post("/v1/capabilities/{capability_id}/ab-compare")
    async def capability_ab_compare(capability_id: str, body: ABCompareRequest) -> dict[str, Any]:
        """Replay a held-out set of traces through baseline + candidate outputs.

        Each replay row carries the same prompt + context but two candidate
        outputs (``baseline_output``, ``candidate_output``). We run the
        capability's auto-eval against each and return per-side aggregates
        plus the per-capability delta.
        """
        capability_store: CapabilityStore = app.state.capability_store
        try:
            spec = capability_store.get(capability_id)
        except CapabilityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"no such capability: {exc}") from exc
        replay_store: ReplaySetStore = app.state.replay_set_store
        run_store: TrainingRunStore = app.state.training_run_store

        if body.replay is not None:
            replay_rows = body.replay
        elif body.replay_set_id is not None:
            replay_set = replay_store.load(body.replay_set_id)
            if replay_set is None or replay_set.capability_id != capability_id:
                raise HTTPException(
                    status_code=404, detail=f"no such replay set: {body.replay_set_id}"
                )
            replay_rows = [ReplayRow.model_validate(row) for row in replay_set.rows]
        else:
            raise HTTPException(status_code=400, detail="provide replay or replay_set_id")

        runtime = local_settings()
        engine = EvalEngine(llm=judge_client(runtime))
        baseline_scores = []
        candidate_scores = []
        for row in replay_rows:
            base_trace = TraceData(
                trace_id=f"{row.trace_id}:baseline",
                project_id=row.project_id or "default",
                input=row.input,
                output=row.baseline_output,
                context=row.context or "",
                tags=dict(row.tags or {}),
            )
            cand_trace = TraceData(
                trace_id=f"{row.trace_id}:candidate",
                project_id=row.project_id or "default",
                input=row.input,
                output=row.candidate_output,
                context=row.context or "",
                tags=dict(row.tags or {}),
            )
            baseline_scores.extend(await engine.evaluate_trace(base_trace, spec))
            candidate_scores.extend(await engine.evaluate_trace(cand_trace, spec))

        baseline_agg = aggregate_score(baseline_scores, spec)
        candidate_agg = aggregate_score(candidate_scores, spec)
        response = {
            "capability_id": capability_id,
            "sample_count": len(replay_rows),
            "baseline": {
                "aggregate_score": baseline_agg,
                "scores": [s.as_dict() for s in baseline_scores],
            },
            "candidate": {
                "aggregate_score": candidate_agg,
                "scores": [s.as_dict() for s in candidate_scores],
            },
            "delta": candidate_agg - baseline_agg,
        }
        if body.run_id is not None:
            run = run_store.load(body.run_id)
            if run is None:
                raise HTTPException(status_code=404, detail=f"no such training run: {body.run_id}")
            run.latest_comparison = {
                "replay_set_id": body.replay_set_id,
                "baseline": {"aggregate_score": baseline_agg},
                "candidate": {"aggregate_score": candidate_agg},
                "delta": candidate_agg - baseline_agg,
                "ts": _now_iso(),
            }
            run.updated_at = _now_iso()
            run_store.save(run)
        return response

    @app.get("/v1/capabilities/{capability_id}/scorecard")
    def capability_scorecard(capability_id: str) -> dict[str, Any]:
        """Return a per-capability scorecard from buffered eval scores.

        Phase 4 returns live aggregates computed from whatever scores have
        been written so far (in-memory buffer + ClickHouse). Later phases
        add trend windows (last N hours / days) and baseline snapshots.
        """
        capability_store: CapabilityStore = app.state.capability_store
        trace_store: TraceStore = app.state.trace_store

        try:
            spec = capability_store.get(capability_id)
        except CapabilityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"no such capability: {exc}") from exc

        rows = trace_store.list_eval_scores(capability_id=capability_id)
        if not rows:
            return {
                "capability_id": capability_id,
                "sample_count": 0,
                "aggregate_score": None,
                "dimensions": [],
            }

        weights = {d.id: d.weight for d in spec.eval_dimensions}
        per_dim: dict[str, list[float]] = {}
        for r in rows:
            per_dim.setdefault(r["dimension"], []).append(float(r["score"]))

        total_w = 0.0
        weighted_sum = 0.0
        dim_summaries: list[dict[str, Any]] = []
        for dim_id, scores in per_dim.items():
            mean = sum(scores) / len(scores)
            w = float(weights.get(dim_id, 1.0))
            total_w += w
            weighted_sum += mean * w
            dim_summaries.append(
                {
                    "dimension": dim_id,
                    "mean_score": mean,
                    "sample_count": len(scores),
                    "weight": w,
                }
            )

        return {
            "capability_id": capability_id,
            "sample_count": len({r["trace_id"] for r in rows}),
            "aggregate_score": weighted_sum / total_w if total_w else None,
            "dimensions": dim_summaries,
        }

    return app


class CreateFromTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: str
    id: str | None = None
    name: str | None = None
    overwrite: bool = False


class CompilerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str


class CompilerCompile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str
    answers: dict[str, str] | None = None


class EvalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trace_id: str
    project_id: str | None = None
    input: str
    output: str
    context: str | None = None
    tags: dict[str, str] | None = None
    capability_ids: list[str] | None = None


class FailingTraceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trace_id: str
    project_id: str | None = None
    input: str
    output: str
    context: str | None = None
    corrected_response: str | None = None
    tags: dict[str, str] | None = None


class ClusterRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    failures: list[FailingTraceInput] | None = None
    failure_ids: list[str] | None = None
    min_cluster_size: int = 3
    summarize: bool = True


class ClusterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    capability_id: str
    label: str
    size: int
    trace_ids: list[str]


class SynthesizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cluster: ClusterInput | None = None
    cluster_id: str | None = None
    failures: list[FailingTraceInput] | None = None
    method: str = "sft"
    generate_missing: bool = True


class FailureReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["needs_correction", "not_useful"]
    note: str | None = None


class CreateTrainingRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability_id: str
    recipe_id: str
    dataset_id: str
    baseline: dict[str, float] | None = None
    allow_backend_fallback: bool = True


class ApplyGateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate: dict[str, float] | None = None
    baseline: dict[str, float] | None = None


class ServedValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replay_set_id: str


class ServedValidationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replay_set_id: str
    job_id: str | None = None


class ActivateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str


class GuidedActionExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool = False


class AutopilotPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    min_corrected_failures: int | None = None
    min_cluster_size: int | None = None
    allowed_training_recipes: list[str] | None = None
    auto_generate_corrections: bool | None = None
    allow_generated_corrections: bool | None = None
    auto_create_dataset: bool | None = None
    auto_start_training: bool | None = None
    auto_run_served_validation: bool | None = None
    auto_promote: bool | None = None
    require_promotion_approval: bool | None = None
    allow_dry_run_fallback: bool | None = None
    require_served_validation: bool | None = None
    max_training_runs_per_day: int | None = None
    promotion_cooldown_seconds: int | None = None
    rollback_mode: Literal["disable_current", "restore_previous"] | None = None


class AutopilotRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger: str = "manual"


class AutopilotApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool
    note: str | None = None


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str
    mode: Literal["disable_current", "restore_previous"] | None = None


class UpdateSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    judge_provider: str | None = None
    judge_model: str | None = None
    embedding_model: str | None = None
    min_cluster_size: int | None = None
    auto_eval_new_traces: bool | None = None
    auto_cluster_failures: bool | None = None


class ReplayRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trace_id: str
    project_id: str | None = None
    input: str
    context: str | None = None
    baseline_output: str
    candidate_output: str
    tags: dict[str, str] | None = None


class ReplaySetWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    rows: list[ReplayRow]


class ABCompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replay: list[ReplayRow] | None = None
    replay_set_id: str | None = None
    run_id: str | None = None


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _run_to_dict(run: TrainingRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "capability_id": run.capability_id,
        "recipe_id": run.recipe_id,
        "dataset_id": run.dataset_id,
        "dataset_path": run.dataset_path,
        "status": run.status,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "artifact": run.artifact,
        "baseline": run.baseline,
        "candidate": run.candidate,
        "gate_verdict": run.gate_verdict,
        "latest_comparison": run.latest_comparison,
        "served_validation": run.served_validation,
        "allow_backend_fallback": run.allow_backend_fallback,
        "error": run.error,
    }


def _json_response(
    payload: dict[str, Any],
    *,
    status_code: int = 200,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    import json as _json

    return Response(
        content=_json.dumps(payload, ensure_ascii=False),
        media_type="application/json",
        status_code=status_code,
        headers=extra_headers or {},
    )


app = create_app()
