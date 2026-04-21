"""FlyChain gateway FastAPI application."""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Any

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
    apply_gate,
    auto_client,
    auto_embedder,
    cluster_failures,
    list_recipes,
    list_templates,
    recipe_by_id,
    select_backend,
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
from flychain_gateway.models_registry import ModelNotFoundError, ModelRegistry, get_registry
from flychain_gateway.otel import get_tracer, make_llm_attributes, setup_tracing
from flychain_gateway.providers.registry import ProviderRouter
from flychain_gateway.schemas import (
    AnthropicMessagesRequest,
    ChatCompletionRequest,
    FeedbackAccepted,
    FeedbackRequest,
    TraceRecord,
)
from flychain_gateway.settings_store import LocalSettings, SettingsStore
from flychain_gateway.trace_store import TraceStore
from flychain_gateway.training_store import (
    AdapterPointerStore,
    TrainingRun,
    TrainingRunStore,
)


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
    local_settings_store = SettingsStore(data_root / "settings.json")

    app.state.settings = settings
    app.state.registry = registry
    app.state.trace_store = store
    app.state.router = router
    app.state.capability_store = capability_store
    app.state.cluster_store = cluster_store
    app.state.dataset_store = dataset_store
    app.state.training_run_store = training_run_store
    app.state.adapter_pointer_store = adapter_pointer_store
    app.state.local_settings_store = local_settings_store

    try:
        yield
    finally:
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

        project_id, _capability_ids, tags = _extract_headers(
            x_flychain_project=x_flychain_project,
            x_flychain_capabilities=x_flychain_capabilities,
            x_flychain_tags=x_flychain_tags,
            default_project_id=settings.default_project_id,
        )

        try:
            resolved = router.resolve_chat(body.model)
        except (ModelNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        payload = body.model_dump(exclude_none=True)
        payload["model"] = resolved.model_id

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

        response_body = dict(result.payload)
        response_body.setdefault("id", trace_id)
        return _json_response(
            response_body,
            status_code=result.raw_status if result.raw_status < 400 else 502,
            extra_headers={"x-flychain-trace-id": trace_id},
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

        project_id, _capability_ids, tags = _extract_headers(
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
        compiler = CapabilityCompiler(llm=auto_client(ollama_model=runtime.judge_model))
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
        compiler = CapabilityCompiler(llm=auto_client(ollama_model=runtime.judge_model))
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
        engine = EvalEngine(llm=auto_client(ollama_model=runtime.judge_model))
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

        return {
            "trace_id": body.trace_id,
            "evaluated_capabilities": list(per_capability.keys()),
            "per_capability": per_capability,
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
        settings = store.save(
            current.model_copy(update=body.model_dump(exclude_none=True))
        )
        return {
            "settings": settings.model_dump(mode="json"),
            "openai_configured": bool(os.environ.get("OPENAI_API_KEY")),
            "anthropic_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
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

        runtime = local_settings()
        result = await cluster_failures(
            capability=spec,
            failures=failures,
            embedder=auto_embedder(ollama_model=runtime.embedding_model),
            llm=auto_client(ollama_model=runtime.judge_model) if body.summarize else None,
            min_cluster_size=body.min_cluster_size,
            summarize=body.summarize,
        )
        cluster_store.save(result)
        return result.as_dict()

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
        cluster = Cluster(
            id=body.cluster.id,
            capability_id=body.cluster.capability_id,
            label=body.cluster.label,
            size=body.cluster.size,
            trace_ids=body.cluster.trace_ids,
        )

        method = body.method.lower()
        if method == "sft":
            runtime = local_settings()
            rows = await synthesize_sft_dataset(
                capability=spec,
                cluster=cluster,
                failures=failures,
                llm=auto_client(ollama_model=runtime.judge_model) if body.generate_missing else None,
                generate_missing=body.generate_missing,
            )
        elif method == "dpo":
            runtime = local_settings()
            rows = await synthesize_dpo_dataset(
                capability=spec,
                cluster=cluster,
                failures=failures,
                llm=auto_client(ollama_model=runtime.judge_model) if body.generate_missing else None,
                generate_missing=body.generate_missing,
            )
        else:
            raise HTTPException(status_code=400, detail="method must be sft or dpo")

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
        return {
            "id": record.id,
            "capability_id": record.capability_id,
            "cluster_id": record.cluster_id,
            "method": record.method,
            "path": record.path,
            "row_count": record.row_count,
        }

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

    @app.post("/v1/training-runs")
    def training_runs_create(body: CreateTrainingRun) -> dict[str, Any]:
        """Create and immediately execute a training run.

        V1 runs synchronously - the dry-run backend is fast and the MLX /
        unsloth backends run out-of-band when configured. Async orchestration
        via arq is available; see ``apps/orchestrator``.
        """
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

        run_id = f"run_{ULID()}"
        now = _now_iso()
        run = TrainingRun(
            id=run_id,
            capability_id=body.capability_id,
            recipe_id=recipe.id,
            dataset_id=body.dataset_id,
            dataset_path=str(dataset_path),
            status="running",
            created_at=now,
            updated_at=now,
            artifact=None,
            baseline=dict(body.baseline or {}),
            candidate={},
        )
        run_store.save(run)

        try:
            backend = select_backend(
                recipe.backend.value, allow_fallback=body.allow_backend_fallback
            )
            artifact = backend.run(
                recipe=recipe,
                dataset_path=dataset_path,
                output_dir=default_data_dir() / "runs" / run_id / "artifacts",
            )
            run.artifact = artifact.as_dict()
            run.status = "trained"
            run.updated_at = _now_iso()
        except Exception as exc:
            run.status = "failed"
            run.error = str(exc)
            run.updated_at = _now_iso()
            run_store.save(run)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        run_store.save(run)
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

    @app.post("/v1/training-runs/{run_id}/apply-gate")
    def training_runs_apply_gate(run_id: str, body: ApplyGateRequest) -> dict[str, Any]:
        """Apply the auto-promote gate to a trained run.

        The caller supplies the candidate's per-capability aggregate scores
        (produced by running the eval suite against the new adapter); the
        baseline comes from the stored run (falling back to the body).
        """
        run_store: TrainingRunStore = app.state.training_run_store
        adapter_store: AdapterPointerStore = app.state.adapter_pointer_store

        run = run_store.load(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no such training run: {run_id}")
        if run.status not in {"trained", "archived", "promoted"}:
            raise HTTPException(
                status_code=409, detail=f"run not in a gate-eligible state: {run.status}"
            )

        try:
            recipe = recipe_by_id(run.recipe_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"recipe missing: {exc}") from exc

        baseline = body.baseline if body.baseline is not None else run.baseline
        verdict = apply_gate(
            target_capability_id=run.capability_id,
            baseline=baseline,
            candidate=body.candidate,
            threshold=recipe.promotion_threshold,
            max_other_regression=recipe.max_other_regression,
        )

        run.baseline = dict(baseline)
        run.candidate = dict(body.candidate)
        run.gate_verdict = verdict.as_dict()
        run.status = "promoted" if verdict.promoted() else "archived"
        run.updated_at = _now_iso()
        run_store.save(run)

        if verdict.promoted() and run.artifact is not None:
            adapter_store.set_active(
                run.capability_id,
                run_id=run.id,
                adapter_dir=run.artifact.get("adapter_dir", ""),
                baseline=run.baseline,
                candidate=run.candidate,
            )

        return {
            "run": _run_to_dict(run),
            "verdict": verdict.as_dict(),
        }

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
        if run.status not in {"trained", "promoted"}:
            raise HTTPException(
                status_code=409,
                detail=f"run not in activatable state: {run.status}",
            )
        if run.artifact is None:
            raise HTTPException(status_code=409, detail="run has no artifact")

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

        runtime = local_settings()
        engine = EvalEngine(llm=auto_client(ollama_model=runtime.judge_model))
        baseline_scores = []
        candidate_scores = []
        for row in body.replay:
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
        return {
            "capability_id": capability_id,
            "sample_count": len(body.replay),
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
    failures: list[FailingTraceInput]
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
    cluster: ClusterInput
    failures: list[FailingTraceInput]
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
    candidate: dict[str, float]
    baseline: dict[str, float] | None = None


class ActivateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str


class UpdateSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
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


class ABCompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replay: list[ReplayRow]


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
