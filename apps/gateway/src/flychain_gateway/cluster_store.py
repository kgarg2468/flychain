"""Filesystem-backed store for clustering results and synthesized datasets.

Both live under ``$FLYCHAIN_DATA_DIR``:

    datasets/<capability_id>/<dataset_id>.jsonl
    clusters/<capability_id>.json

This keeps the orchestrator, the gateway, and anyone with ``cat`` on the
same page during v1 laptop-first mode.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from flychain_capability_compiler import ClusteringResult, SynthesizedDataset


class ClusterStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, capability_id: str) -> Path:
        return self.directory / f"{capability_id}.json"

    def save(self, result: ClusteringResult) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(result.capability_id).write_text(
            json.dumps(result.as_dict(), ensure_ascii=False, indent=2)
        )

    def load(self, capability_id: str) -> dict[str, Any] | None:
        path = self._path(capability_id)
        if not path.exists():
            return None
        return json.loads(path.read_text())


class DatasetStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index_path = self.directory / "index.json"

    def _load_index(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        return json.loads(self.index_path.read_text())

    def _save_index(self, entries: list[dict[str, Any]]) -> None:
        self.index_path.write_text(json.dumps(entries, indent=2))

    def record(self, dataset: SynthesizedDataset) -> None:
        entries = self._load_index()
        entries.append(asdict(dataset))
        self._save_index(entries)

    def list_for_capability(self, capability_id: str) -> list[dict[str, Any]]:
        return [e for e in self._load_index() if e["capability_id"] == capability_id]

    def all(self) -> list[dict[str, Any]]:
        return self._load_index()

    def resolve_path(self, dataset_id: str) -> Path | None:
        for entry in self._load_index():
            if entry["id"] == dataset_id:
                return Path(entry["path"])
        return None
