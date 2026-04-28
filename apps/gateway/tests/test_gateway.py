"""Gateway smoke + chat-completions + /v1/feedback tests (Phase 1).

These tests stub out the HTTP client used by providers so no real network
calls are made. The trace store falls back to its in-memory buffer because
ClickHouse is not running during unit tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from flychain_gateway.main import create_app
from flychain_gateway.settings_store import LocalSettings


class _MockTransport(httpx.MockTransport):
    def __init__(self, responder):
        super().__init__(responder)


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"function": function, "args": args, "kwargs": kwargs})
        return {"job_id": f"job-{len(self.calls)}"}


def _openai_responder(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/v1/chat/completions"
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "hello from mock"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        },
    )


def _anthropic_responder(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/v1/messages"
    return httpx.Response(
        200,
        json={
            "id": "msg_mock",
            "type": "message",
            "role": "assistant",
            "model": "claude-3-5-haiku-latest",
            "content": [{"type": "text", "text": "hi"}],
            "usage": {"input_tokens": 8, "output_tokens": 4},
        },
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    """Build a gateway TestClient with upstream HTTP calls intercepted."""

    original_async_client = httpx.AsyncClient

    def _factory(path_responder):
        def _ctor(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(path_responder)
            return original_async_client(*args, **kwargs)

        return _ctor

    def _dispatch(request: httpx.Request) -> httpx.Response:
        if "anthropic" in request.url.host:
            return _anthropic_responder(request)
        return _openai_responder(request)

    monkeypatch.setattr(httpx, "AsyncClient", _factory(_dispatch))
    monkeypatch.setenv("FLYCHAIN_DATA_DIR", str(tmp_path / "flychain-data"))
    monkeypatch.setenv("FLYCHAIN_CLICKHOUSE_URL", "http://localhost:1/flychain")

    app = create_app()
    with TestClient(app) as tc:
        yield tc


def test_healthz(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_list_models(client: TestClient) -> None:
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    providers = resp.json()["providers"]
    assert set(providers) >= {"openai", "anthropic", "local-ollama"}


def test_chat_completions_openai_path(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={
            "x-flychain-project": "unit-test",
            "x-flychain-capabilities": "instruction-following,groundedness",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "hello from mock"
    assert resp.headers["x-flychain-trace-id"].startswith("trace_")

    traces = client.get("/debug/traces").json()
    assert traces, "trace should have been written to buffer"
    row = traces[-1]
    assert row["project_id"] == "unit-test"
    assert row["capability_ids"] == ["instruction-following", "groundedness"]
    assert row["provider"] == "openai"
    assert row["prompt_tokens"] == 10
    assert row["completion_tokens"] == 5
    assert row["total_tokens"] == 15
    # gpt-4o-mini: (10/1000)*0.00015 + (5/1000)*0.0006 = 0.0000015 + 0.000003
    assert abs(row["cost_usd"] - 0.0000045) < 1e-9


def test_chat_completions_routes_ollama(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "llama3.2:3b-instruct",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200
    traces = client.get("/debug/traces").json()
    row = traces[-1]
    assert row["provider"] == "local-ollama"
    assert row["cost_usd"] == 0.0


def test_chat_completions_unknown_model(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "nope-1", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 404


def test_chat_completions_rejects_anthropic_on_chat_route(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "claude-3-5-haiku-latest",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 404


def test_messages_anthropic_path(client: TestClient) -> None:
    resp = client.post(
        "/v1/messages",
        json={
            "model": "claude-3-5-haiku-latest",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 128,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["content"][0]["text"] == "hi"
    traces = client.get("/debug/traces").json()
    row = traces[-1]
    assert row["provider"] == "anthropic"
    assert row["prompt_tokens"] == 8
    assert row["completion_tokens"] == 4
    assert row["total_tokens"] == 12


def test_chat_completions_enqueues_auto_eval_when_enabled(client: TestClient) -> None:
    queue = _FakeQueue()
    client.app.state.job_queue = queue
    client.app.state.local_settings_store.save(LocalSettings(auto_eval_new_traces=True))

    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "What does the policy say?"}],
        },
        headers={
            "x-flychain-project": "unit-test",
            "x-flychain-capabilities": "groundedness",
            "x-flychain-tags": "task=rag",
        },
    )
    assert resp.status_code == 200, resp.text
    trace_id = resp.headers["x-flychain-trace-id"]

    assert queue.calls == [
        {
            "function": "evaluate_trace",
            "args": (),
            "kwargs": {
                "trace_id": trace_id,
                "project_id": "unit-test",
                "input_text": "What does the policy say?",
                "output_text": "hello from mock",
                "context": "",
                "tags": {"task": "rag"},
                "capability_ids": ["groundedness"],
            },
        }
    ]


def test_messages_enqueues_auto_eval_when_enabled(client: TestClient) -> None:
    queue = _FakeQueue()
    client.app.state.job_queue = queue
    client.app.state.local_settings_store.save(LocalSettings(auto_eval_new_traces=True))

    resp = client.post(
        "/v1/messages",
        json={
            "model": "claude-3-5-haiku-latest",
            "messages": [{"role": "user", "content": "Summarize the doc"}],
            "max_tokens": 128,
        },
        headers={
            "x-flychain-project": "anthropic-test",
            "x-flychain-capabilities": "groundedness",
            "x-flychain-tags": "task=rag",
        },
    )
    assert resp.status_code == 200, resp.text
    trace_id = resp.headers["x-flychain-trace-id"]

    assert queue.calls == [
        {
            "function": "evaluate_trace",
            "args": (),
            "kwargs": {
                "trace_id": trace_id,
                "project_id": "anthropic-test",
                "input_text": "Summarize the doc",
                "output_text": "hi",
                "context": "",
                "tags": {"task": "rag"},
                "capability_ids": ["groundedness"],
            },
        }
    ]


def test_feedback_endpoint(client: TestClient) -> None:
    resp = client.post(
        "/v1/feedback",
        json={
            "trace_id": "trace_abc",
            "project_id": "p1",
            "thumb": "down",
            "score": -2,
            "comment": "wrong answer",
            "corrected_response": "the correct answer is 42",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["recorded"] is True
    assert body["trace_id"] == "trace_abc"
    assert body["feedback_id"].startswith("fb_")

    rows = client.get("/debug/feedback").json()
    assert rows[-1]["trace_id"] == "trace_abc"
    assert rows[-1]["thumb"] == "down"
    assert rows[-1]["score"] == -2
    assert rows[-1]["corrected_response"] == "the correct answer is 42"


def test_streaming_not_supported(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert resp.status_code == 400
