"""FlyChain gateway FastAPI application."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from typing import Any
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
from flychain_gateway.capability_store import (
    CapabilityExistsError,
    CapabilityNotFoundError,
    CapabilityStore,
    default_data_dir,
    slugify,
)
from flychain_gateway.cluster_store import ClusterStore, DatasetStore
from flychain_gateway.config import Settings, get_settings
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
    replay_set_store = ReplaySetStore(data_root / "replay-sets")
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
    app.state.replay_set_store = replay_set_store
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
        feedback_rows = sorted(
            trace_store.list_feedback(),
            key=lambda row: row.get("ts", ""),
            reverse=True,
        )
        feedback_by_trace: dict[str, dict[str, Any]] = {}
        for row in feedback_rows:
            feedback_by_trace.setdefault(row["trace_id"], row)

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
                    "corrected_response": feedback.get("corrected_response") or None,
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
        )
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

        auto_clusters = await maybe_auto_cluster_failures(
            specs=specs,
            failed_capability_ids={score.capability_id for score in all_scores if not score.passed},
        )

        return {
            "trace_id": body.trace_id,
            "evaluated_capabilities": list(per_capability.keys()),
            "per_capability": per_capability,
            "auto_clusters": auto_clusters,
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
        }

    @app.get("/v1/capabilities/{capability_id}/failures")
    def capabilities_failures(capability_id: str) -> dict[str, Any]:
        return {
            "capability_id": capability_id,
            "failures": derive_failures(capability_id),
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
        run_store: TrainingRunStore = app.state.training_run_store
        replay_store: ReplaySetStore = app.state.replay_set_store
        run = run_store.load(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no such training run: {run_id}")
        replay_set = replay_store.load(body.replay_set_id)
        if replay_set is None or replay_set.capability_id != run.capability_id:
            raise HTTPException(status_code=404, detail=f"no such replay set: {body.replay_set_id}")
        queue = require_job_queue()

        retry_payload: dict[str, Any] = {
            "function": "run_served_validation",
            "kwargs": {"run_id": run.id, "replay_set_id": body.replay_set_id},
        }
        job = create_job(
            job_type="served_validation",
            capability_id=run.capability_id,
            run_id=run.id,
            replay_set_id=body.replay_set_id,
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
            "replay_set_id": body.replay_set_id,
            "job_id": job.id,
            "queued_at": _now_iso(),
        }
        run.updated_at = _now_iso()
        run_store.save(run)

        try:
            await queue.enqueue_job(
                "run_served_validation",
                run_id=run.id,
                replay_set_id=body.replay_set_id,
                job_id=job.id,
            )
        except Exception as exc:
            app.state.job_store.fail(job.id, error=f"queue enqueue failed: {exc}")
            run.status = "validation-failed"
            run.error = f"queue enqueue failed: {exc}"
            run.updated_at = _now_iso()
            run_store.save(run)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
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

        adapter_store.set_active(
            capability_id,
            run_id=run.id,
            adapter_dir=str(run.artifact.get("adapter_dir", "")),
            baseline=run.baseline,
            candidate=run.candidate,
        )
        run.status = "promoted"
        run.updated_at = _now_iso()
        run_store.save(run)
        return {"capability_id": capability_id, "active_run_id": run.id}

    @app.delete("/v1/capabilities/{capability_id}/active-adapter", status_code=204)
    def capability_deactivate(capability_id: str) -> Response:
        """Clear the active adapter pointer (revert to base model)."""
        adapter_store: AdapterPointerStore = app.state.adapter_pointer_store
        path = adapter_store._path(capability_id)  # noqa: SLF001
        if path.exists():
            path.unlink()
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
