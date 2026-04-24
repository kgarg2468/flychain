"""Filesystem-backed replay-set store for A/B comparisons."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ReplaySet:
    id: str
    capability_id: str
    name: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class ReplaySetStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, replay_set_id: str) -> Path:
        return self.directory / f"{replay_set_id}.json"

    def save(self, replay_set: ReplaySet) -> None:
        self._path(replay_set.id).write_text(json.dumps(asdict(replay_set), indent=2))

    def load(self, replay_set_id: str) -> ReplaySet | None:
        path = self._path(replay_set_id)
        if not path.exists():
            return None
        return ReplaySet(**json.loads(path.read_text()))

    def list_for_capability(self, capability_id: str) -> list[ReplaySet]:
        items: list[ReplaySet] = []
        for path in sorted(self.directory.glob("*.json")):
            replay_set = ReplaySet(**json.loads(path.read_text()))
            if replay_set.capability_id == capability_id:
                items.append(replay_set)
        return items
