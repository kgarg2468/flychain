"""File-backed background job records."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ulid import ULID

JOB_TYPE_DEFAULTS: dict[str, dict[str, int | None]] = {
    "auto_eval": {"max_retries": 2, "timeout_seconds": 120},
    "cluster": {"max_retries": 1, "timeout_seconds": 120},
    "dataset_synthesis": {"max_retries": 1, "timeout_seconds": 120},
    "training": {"max_retries": 0, "timeout_seconds": None},
    "promotion_gate": {"max_retries": 1, "timeout_seconds": 60},
    "served_validation": {"max_retries": 1, "timeout_seconds": 300},
}


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class JobRecord:
    id: str
    type: str
    status: str
    created_at: str
    updated_at: str
    capability_id: str | None = None
    trace_ids: list[str] = field(default_factory=list)
    cluster_id: str | None = None
    dataset_id: str | None = None
    run_id: str | None = None
    replay_set_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    retry_count: int = 0
    max_retries: int = 0
    timeout_seconds: int | None = None
    next_retry_at: str | None = None
    worker_id: str | None = None
    error: str | None = None
    retry_payload: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class JobStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str) -> Path:
        return self.directory / f"{job_id}.json"

    def create(
        self,
        *,
        job_type: str,
        status: str = "queued",
        capability_id: str | None = None,
        trace_ids: list[str] | None = None,
        cluster_id: str | None = None,
        dataset_id: str | None = None,
        run_id: str | None = None,
        replay_set_id: str | None = None,
        max_retries: int | None = None,
        timeout_seconds: int | None = None,
        retry_payload: dict[str, Any] | None = None,
    ) -> JobRecord:
        now = _now_iso()
        defaults = JOB_TYPE_DEFAULTS.get(job_type, {})
        effective_max_retries = (
            int(max_retries)
            if max_retries is not None
            else int(defaults.get("max_retries") or 0)
        )
        effective_timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else defaults.get("timeout_seconds")
        )
        job = JobRecord(
            id=f"job_{ULID()}",
            type=job_type,
            status=status,
            created_at=now,
            updated_at=now,
            capability_id=capability_id,
            trace_ids=list(trace_ids or []),
            cluster_id=cluster_id,
            dataset_id=dataset_id,
            run_id=run_id,
            replay_set_id=replay_set_id,
            max_retries=effective_max_retries,
            timeout_seconds=effective_timeout_seconds,
            retry_payload=retry_payload,
        )
        self.save(job)
        return job

    def save(self, job: JobRecord) -> None:
        self._path(job.id).write_text(json.dumps(job.as_dict(), indent=2))

    def load(self, job_id: str) -> JobRecord | None:
        path = self._path(job_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return JobRecord(**data)

    def list(self, *, limit: int = 100) -> list[JobRecord]:
        jobs = [JobRecord(**json.loads(path.read_text())) for path in self.directory.glob("*.json")]
        jobs.sort(key=lambda job: job.created_at, reverse=True)
        return jobs[:limit]

    def start(self, job_id: str, *, worker_id: str | None = None) -> JobRecord | None:
        job = self.load(job_id)
        if job is None:
            return None
        now = _now_iso()
        job.status = "running"
        job.started_at = job.started_at or now
        job.updated_at = now
        job.worker_id = worker_id
        job.error = None
        self.save(job)
        return job

    def succeed(self, job_id: str) -> JobRecord | None:
        job = self.load(job_id)
        if job is None:
            return None
        now = _now_iso()
        job.status = "succeeded"
        job.finished_at = now
        job.updated_at = now
        job.error = None
        job.duration_ms = _duration_ms(job.started_at, job.finished_at)
        self.save(job)
        return job

    def fail(self, job_id: str, *, error: str, timed_out: bool = False) -> JobRecord | None:
        job = self.load(job_id)
        if job is None:
            return None
        now = _now_iso()
        job.status = "timed_out" if timed_out else "failed"
        job.finished_at = now
        job.updated_at = now
        job.error = error
        job.duration_ms = _duration_ms(job.started_at, job.finished_at)
        self.save(job)
        return job

    def timeout(self, job_id: str, *, error: str) -> JobRecord | None:
        return self.fail(job_id, error=error, timed_out=True)

    def queue_retry(self, job_id: str) -> JobRecord | None:
        job = self.load(job_id)
        if job is None:
            return None
        now = _now_iso()
        job.status = "retrying"
        job.retry_count += 1
        job.updated_at = now
        job.next_retry_at = now
        job.started_at = None
        job.finished_at = None
        job.duration_ms = None
        job.error = None
        self.save(job)
        return job


def _duration_ms(started_at: str | None, finished_at: str | None) -> int | None:
    if not started_at or not finished_at:
        return None
    from datetime import datetime

    started = datetime.fromisoformat(started_at)
    finished = datetime.fromisoformat(finished_at)
    return int((finished - started).total_seconds() * 1000)
