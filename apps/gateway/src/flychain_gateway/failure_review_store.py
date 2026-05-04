"""File-backed operator review state for capability failures."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

FailureReviewStatus = Literal["needs_correction", "not_useful"]


@dataclass(slots=True)
class FailureReview:
    capability_id: str
    trace_id: str
    status: FailureReviewStatus
    note: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


class FailureReviewStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, capability_id: str, trace_id: str) -> Path:
        cap_dir = self.directory / _safe_name(capability_id)
        cap_dir.mkdir(parents=True, exist_ok=True)
        return cap_dir / f"{_safe_name(trace_id)}.json"

    def save(
        self,
        *,
        capability_id: str,
        trace_id: str,
        status: FailureReviewStatus,
        note: str = "",
        updated_at: str = "",
    ) -> FailureReview:
        review = FailureReview(
            capability_id=capability_id,
            trace_id=trace_id,
            status=status,
            note=note,
            updated_at=updated_at,
        )
        self._path(capability_id, trace_id).write_text(json.dumps(review.as_dict(), indent=2))
        return review

    def get(self, capability_id: str, trace_id: str) -> FailureReview | None:
        path = self._path(capability_id, trace_id)
        if not path.exists():
            return None
        return FailureReview(**json.loads(path.read_text()))

    def list_for_capability(self, capability_id: str) -> list[FailureReview]:
        cap_dir = self.directory / _safe_name(capability_id)
        if not cap_dir.exists():
            return []
        return [FailureReview(**json.loads(path.read_text())) for path in sorted(cap_dir.glob("*.json"))]


def _safe_name(value: str) -> str:
    return value.replace("/", "_").replace("\\", "_")
