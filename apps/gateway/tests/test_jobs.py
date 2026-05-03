"""Gateway job status API tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from flychain_gateway.main import create_app


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"function": function, "args": args, "kwargs": kwargs})
        return {"job_id": f"arq-{len(self.calls)}"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("FLYCHAIN_DATA_DIR", str(tmp_path / "flychain-data"))
    monkeypatch.setenv("FLYCHAIN_CLICKHOUSE_URL", "http://localhost:1/flychain")
    app = create_app()
    with TestClient(app) as tc:
        tc.app.state.job_queue = _FakeQueue()
        yield tc


def test_jobs_list_detail_and_retry(client: TestClient) -> None:
    job = client.app.state.job_store.create(
        job_type="auto_eval",
        capability_id="groundedness",
        trace_ids=["trace_1"],
        max_retries=2,
        retry_payload={
            "function": "evaluate_trace",
            "kwargs": {
                "trace_id": "trace_1",
                "project_id": "p1",
                "input_text": "q",
                "output_text": "bad",
            },
        },
    )
    client.app.state.job_store.fail(job.id, error="judge timeout", timed_out=True)

    listed = client.get("/v1/jobs").json()
    assert listed["jobs"][0]["id"] == job.id
    assert listed["jobs"][0]["status"] == "timed_out"
    assert listed["jobs"][0]["error"] == "judge timeout"

    detail = client.get(f"/v1/jobs/{job.id}").json()
    assert detail["id"] == job.id
    assert detail["retry_count"] == 0
    assert detail["max_retries"] == 2

    retry = client.post(f"/v1/jobs/{job.id}/retry")
    assert retry.status_code == 202, retry.text
    retried = retry.json()
    assert retried["status"] == "retrying"
    assert retried["retry_count"] == 1
    assert retried["next_retry_at"] is not None
    assert client.app.state.job_queue.calls == [
        {
            "function": "evaluate_trace",
            "args": (),
            "kwargs": {
                "job_id": job.id,
                "trace_id": "trace_1",
                "project_id": "p1",
                "input_text": "q",
                "output_text": "bad",
            },
        }
    ]


def test_retry_rejects_non_retryable_job(client: TestClient) -> None:
    job = client.app.state.job_store.create(
        job_type="training",
        status="failed",
        run_id="run_1",
        max_retries=0,
        retry_payload={"function": "run_training_recipe", "kwargs": {"run_id": "run_1"}},
    )

    resp = client.post(f"/v1/jobs/{job.id}/retry")

    assert resp.status_code == 409


def test_job_store_applies_type_defaults(client: TestClient) -> None:
    auto_eval = client.app.state.job_store.create(job_type="auto_eval")
    training = client.app.state.job_store.create(job_type="training")
    validation = client.app.state.job_store.create(job_type="served_validation")

    assert auto_eval.max_retries == 2
    assert auto_eval.timeout_seconds is not None
    assert training.max_retries == 0
    assert validation.max_retries == 1
    assert validation.timeout_seconds is not None


def test_running_job_timeout_is_recorded(client: TestClient) -> None:
    job = client.app.state.job_store.create(job_type="served_validation")
    client.app.state.job_store.start(job.id, worker_id="worker-1")

    timed_out = client.app.state.job_store.timeout(job.id, error="validation exceeded 300s")

    assert timed_out is not None
    assert timed_out.status == "timed_out"
    assert timed_out.error == "validation exceeded 300s"
    assert timed_out.worker_id == "worker-1"
    assert timed_out.started_at is not None
    assert timed_out.finished_at is not None
    assert timed_out.duration_ms is not None
