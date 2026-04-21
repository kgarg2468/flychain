"""Local non-secret runtime settings for the FlyChain gateway."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class LocalSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judge_model: str = "llama3.2:3b"
    embedding_model: str = "nomic-embed-text"
    min_cluster_size: int = Field(default=3, ge=2, le=64)
    auto_eval_new_traces: bool = False
    auto_cluster_failures: bool = False


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> LocalSettings:
        if not self.path.exists():
            return LocalSettings()
        data = json.loads(self.path.read_text())
        return LocalSettings.model_validate(data)

    def save(self, settings: LocalSettings) -> LocalSettings:
        self.path.write_text(json.dumps(settings.model_dump(mode="json"), indent=2))
        return settings
