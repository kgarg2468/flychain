"""Training run registry + adapter pointer (v1 file-backed).

A "training run" is the full journey for a candidate adapter:
    dataset -> recipe -> training backend -> artifact -> evals -> gate.

For v1 we persist it as JSON under ``$FLYCHAIN_DATA_DIR/runs/``. The active
adapter pointer per capability lives under ``pointers/<capability_id>.json``.
"""

from __future__ import annotations

import builtins
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class TrainingRun:
    id: str
    capability_id: str
    recipe_id: str
    dataset_id: str
    dataset_path: str
    status: str  # queued | running | trained | gate-queued | gate-running | promoted | archived | failed
    created_at: str
    updated_at: str
    artifact: dict[str, Any] | None = None
    baseline: dict[str, float] = field(default_factory=dict)
    candidate: dict[str, float] = field(default_factory=dict)
    gate_verdict: dict[str, Any] | None = None
    latest_comparison: dict[str, Any] | None = None
    served_validation: dict[str, Any] | None = None
    allow_backend_fallback: bool = True
    error: str | None = None


class TrainingRunStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        return self.directory / f"{run_id}.json"

    def save(self, run: TrainingRun) -> None:
        self._path(run.id).write_text(json.dumps(asdict(run), indent=2))

    def load(self, run_id: str) -> TrainingRun | None:
        path = self._path(run_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        data.setdefault("served_validation", None)
        return TrainingRun(**data)

    def list(self) -> list[TrainingRun]:
        runs: list[TrainingRun] = []
        for path in sorted(self.directory.glob("*.json")):
            data = json.loads(path.read_text())
            data.setdefault("served_validation", None)
            runs.append(TrainingRun(**data))
        return runs

    def list_for_capability(self, capability_id: str) -> builtins.list[TrainingRun]:
        return [r for r in self.list() if r.capability_id == capability_id]


class AdapterPointerStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, capability_id: str) -> Path:
        return self.directory / f"{capability_id}.json"

    def set_active(
        self,
        capability_id: str,
        *,
        run_id: str,
        adapter_dir: str,
        baseline: dict[str, float],
        candidate: dict[str, float],
    ) -> None:
        payload = {
            "capability_id": capability_id,
            "active_run_id": run_id,
            "adapter_dir": adapter_dir,
            "baseline": baseline,
            "candidate": candidate,
        }
        self._path(capability_id).write_text(json.dumps(payload, indent=2))

    def get(self, capability_id: str) -> dict[str, Any] | None:
        path = self._path(capability_id)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def clear(self, capability_id: str) -> None:
        path = self._path(capability_id)
        if path.exists():
            path.unlink()
