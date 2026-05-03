"""Tests for the gateway /v1/eval endpoint (Phase 4)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from flychain_gateway import main as gw_main
from flychain_gateway.schemas import TraceRecord
from flychain_gateway.settings_store import LocalSettings


class FakeLLM:
    provider = "fake"
    model = "fake"

    def __init__(self, scores: list[float]) -> None:
        self._responses = [
            json.dumps({"score": s, "passed": s >= 0.75, "reason": f"s={s}"}) for s in scores
        ]

    async def complete(self, *, system: str, user: str, json_mode: bool = False) -> str:
        if not self._responses:
            raise AssertionError("FakeLLM: out of responses")
        return self._responses.pop(0)


class FakeEmbedder:
    provider = "fake"
    model = "fake"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(i), float(i)] for i, _text in enumerate(texts)]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("FLYCHAIN_DATA_DIR", str(tmp_path / "flychain-data"))
    monkeypatch.setenv("FLYCHAIN_CLICKHOUSE_URL", "http://localhost:1/flychain")

    def _fake_factory(*_args, **_kwargs):
        # Generous response budget so a request that matches multiple
        # capabilities (each with up to 3 dimensions) still has enough
        # judgements queued.
        return FakeLLM([0.9, 0.85, 0.8, 0.75, 0.95, 0.7, 0.88, 0.6, 0.92, 0.5])

    monkeypatch.setattr(gw_main, "auto_client", _fake_factory)

    from flychain_gateway.main import create_app

    with TestClient(create_app()) as tc:
        yield tc


def _mk_groundedness(client: TestClient) -> None:
    resp = client.post(
        "/v1/capabilities/from-template",
        json={"template_id": "groundedness"},
    )
    assert resp.status_code == 201, resp.text


def _seed_trace(client: TestClient, trace_id: str, *, tags: dict[str, str] | None = None) -> None:
    store = client.app.state.trace_store
    asyncio.run(
        store.insert_trace(
            TraceRecord(
                trace_id=trace_id,
                project_id="p1",
                provider="openai",
                model="gpt-4o-mini",
                method="chat.completions",
                request={"messages": [{"role": "user", "content": "What does page 12 say?"}]},
                response={
                    "choices": [
                        {"message": {"content": "Page 12 says onboarding is manual."}}
                    ]
                },
                status="ok",
                tags=tags or {"task": "rag"},
            )
        )
    )


def test_eval_returns_scores_for_matching_capability(client: TestClient) -> None:
    _mk_groundedness(client)
    resp = client.post(
        "/v1/eval",
        json={
            "trace_id": "trace_1",
            "project_id": "p1",
            "input": "What does page 12 say about onboarding?",
            "output": "Page 12 says onboarding is self-serve.",
            "context": "Page 12: Users onboard via self-serve signup.",
            "tags": {"task": "rag"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["trace_id"] == "trace_1"
    assert body["evaluated_capabilities"] == ["groundedness"]
    per = body["per_capability"]["groundedness"]
    assert 0.0 <= per["aggregate_score"] <= 1.0
    assert len(per["scores"]) == 3  # 3 eval dimensions in the template

    rows = client.get("/debug/eval-scores").json()
    assert len(rows) == 3
    assert all(r["capability_id"] == "groundedness" for r in rows)
    assert all(r["evaluator_type"] == "llm_judge" for r in rows)
    assert all(r["evaluator_source"] == "fake:fake" for r in rows)


def test_eval_persists_deterministic_evaluator_proof(client: TestClient) -> None:
    resp = client.post(
        "/v1/capabilities",
        json={
            "id": "adapter-sentinel",
            "name": "Adapter Sentinel",
            "description": "Return the exact adapter sentinel token.",
            "eval_dimensions": [
                {
                    "id": "exact_sentinel",
                    "description": "Must return exactly ADAPTER_SENTINEL_OK.",
                    "evaluator": {
                        "mode": "deterministic",
                        "deterministic": {
                            "type": "exact_match",
                            "expected": "ADAPTER_SENTINEL_OK",
                            "normalize": {"trim": True},
                        },
                    },
                }
            ],
        },
    )
    assert resp.status_code == 201, resp.text

    eval_resp = client.post(
        "/v1/eval",
        json={
            "trace_id": "trace_fake_token",
            "project_id": "p1",
            "input": "What is the FlyChain adapter sentinel token?",
            "output": "0xADAPTER_SENTINEL_OK",
            "capability_ids": ["adapter-sentinel"],
        },
    )

    assert eval_resp.status_code == 200, eval_resp.text
    score = eval_resp.json()["per_capability"]["adapter-sentinel"]["scores"][0]
    assert score["passed"] is False
    assert score["evaluator_type"] == "deterministic"
    assert score["evaluator_source"] == "deterministic:exact_match"

    rows = client.get("/debug/eval-scores").json()
    assert rows[0]["evaluator_type"] == "deterministic"
    assert rows[0]["evaluator_source"] == "deterministic:exact_match"


def test_eval_uses_explicit_judge_provider_setting(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: dict[str, Any] = {}

    def _factory(*_args: Any, **kwargs: Any) -> FakeLLM:
        recorded.update(kwargs)
        return FakeLLM([0.9, 0.9, 0.9])

    monkeypatch.setattr(gw_main, "auto_client", _factory)
    _mk_groundedness(client)
    settings_resp = client.put(
        "/v1/settings",
        json={"judge_provider": "local-ollama", "judge_model": "llama3.2:3b"},
    )
    assert settings_resp.status_code == 200, settings_resp.text

    resp = client.post(
        "/v1/eval",
        json={
            "trace_id": "trace_explicit_judge",
            "project_id": "p1",
            "input": "What does page 12 say?",
            "output": "Page 12 says onboarding is self-serve.",
            "context": "Page 12: Users onboard via self-serve signup.",
            "tags": {"task": "rag"},
        },
    )

    assert resp.status_code == 200, resp.text
    assert recorded["prefer"] == "local-ollama"
    assert recorded["ollama_model"] == "llama3.2:3b"


def test_eval_skips_capabilities_that_dont_match_slice(client: TestClient) -> None:
    _mk_groundedness(client)
    resp = client.post(
        "/v1/eval",
        json={
            "trace_id": "trace_2",
            "project_id": "p1",
            "input": "hi",
            "output": "hi back",
            "tags": {"task": "chat"},  # groundedness needs task=rag
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["evaluated_capabilities"] == []
    assert body["per_capability"] == {}


def test_eval_explicit_capability_ids_404_on_unknown(client: TestClient) -> None:
    resp = client.post(
        "/v1/eval",
        json={
            "trace_id": "t",
            "project_id": "p",
            "input": "i",
            "output": "o",
            "capability_ids": ["does-not-exist"],
        },
    )
    assert resp.status_code == 404


def test_eval_runs_all_tracked_capabilities_by_default(client: TestClient) -> None:
    client.post("/v1/capabilities/from-template", json={"template_id": "groundedness"})
    client.post("/v1/capabilities/from-template", json={"template_id": "instruction-following"})
    resp = client.post(
        "/v1/eval",
        json={
            "trace_id": "t3",
            "project_id": "p1",
            "input": "output JSON with these keys: a, b",
            "output": '{"a": 1, "b": 2}',
            "context": "",
            "tags": {"task": "rag"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # Groundedness matches via tag rule; instruction-following via semantic rule (always-match in v1).
    assert set(body["evaluated_capabilities"]) == {"groundedness", "instruction-following"}


def test_eval_auto_clusters_new_failures_when_enabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mk_groundedness(client)
    _seed_trace(client, "trace-auto-cluster")
    client.app.state.local_settings_store.save(
        LocalSettings(auto_cluster_failures=True, min_cluster_size=3)
    )

    def _failing_client(*_args: Any, **_kwargs: Any) -> FakeLLM:
        return FakeLLM([0.2, 0.3, 0.4, 0.9])

    monkeypatch.setattr(gw_main, "auto_client", _failing_client)
    monkeypatch.setattr(gw_main, "auto_embedder", lambda *_args, **_kwargs: FakeEmbedder())

    resp = client.post(
        "/v1/eval",
        json={
            "trace_id": "trace-auto-cluster",
            "project_id": "p1",
            "input": "What does page 12 say?",
            "output": "Page 12 says onboarding is manual.",
            "context": "Page 12: onboarding is self-serve.",
            "tags": {"task": "rag"},
        },
    )

    assert resp.status_code == 200, resp.text
    clusters = client.get("/v1/capabilities/groundedness/clusters").json()
    assert clusters["clusters"]
    assert clusters["clusters"][0]["trace_ids"] == ["trace-auto-cluster"]


def test_eval_does_not_auto_cluster_when_disabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mk_groundedness(client)
    _seed_trace(client, "trace-no-cluster")

    def _failing_client(*_args: Any, **_kwargs: Any) -> FakeLLM:
        return FakeLLM([0.2, 0.3, 0.4])

    monkeypatch.setattr(gw_main, "auto_client", _failing_client)
    monkeypatch.setattr(gw_main, "auto_embedder", lambda *_args, **_kwargs: FakeEmbedder())

    resp = client.post(
        "/v1/eval",
        json={
            "trace_id": "trace-no-cluster",
            "project_id": "p1",
            "input": "What does page 12 say?",
            "output": "Page 12 says onboarding is manual.",
            "context": "Page 12: onboarding is self-serve.",
            "tags": {"task": "rag"},
        },
    )

    assert resp.status_code == 200, resp.text
    clusters = client.get("/v1/capabilities/groundedness/clusters").json()
    assert clusters["clusters"] == []


def test_eval_does_not_auto_cluster_passing_scores(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mk_groundedness(client)
    _seed_trace(client, "trace-passing")
    client.app.state.local_settings_store.save(LocalSettings(auto_cluster_failures=True))
    monkeypatch.setattr(gw_main, "auto_embedder", lambda *_args, **_kwargs: FakeEmbedder())

    resp = client.post(
        "/v1/eval",
        json={
            "trace_id": "trace-passing",
            "project_id": "p1",
            "input": "What does page 12 say about onboarding?",
            "output": "Page 12 says onboarding is self-serve.",
            "context": "Page 12: Users onboard via self-serve signup.",
            "tags": {"task": "rag"},
        },
    )

    assert resp.status_code == 200, resp.text
    clusters = client.get("/v1/capabilities/groundedness/clusters").json()
    assert clusters["clusters"] == []
