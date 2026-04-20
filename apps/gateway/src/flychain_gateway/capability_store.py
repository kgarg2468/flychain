"""Filesystem-backed capability store.

For v1 local-only mode, persisted capabilities live as YAML files in
``$FLYCHAIN_DATA_DIR/capabilities``. This keeps the gateway stateless and
allows the orchestrator (and users with a text editor) to read the same
files.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from flychain_capability_compiler import CapabilitySpec


class CapabilityExistsError(Exception):
    pass


class CapabilityNotFoundError(KeyError):
    pass


class CapabilityStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, capability_id: str) -> Path:
        return self.directory / f"{capability_id}.yaml"

    def list(self) -> list[CapabilitySpec]:
        specs: list[CapabilitySpec] = []
        for yaml_path in sorted(self.directory.glob("*.yaml")):
            data = yaml.safe_load(yaml_path.read_text()) or {}
            specs.append(CapabilitySpec.model_validate(data))
        return specs

    def get(self, capability_id: str) -> CapabilitySpec:
        path = self.path_for(capability_id)
        if not path.exists():
            raise CapabilityNotFoundError(capability_id)
        data = yaml.safe_load(path.read_text()) or {}
        return CapabilitySpec.model_validate(data)

    def exists(self, capability_id: str) -> bool:
        return self.path_for(capability_id).exists()

    def create(self, spec: CapabilitySpec, *, overwrite: bool = False) -> CapabilitySpec:
        path = self.path_for(spec.id)
        if path.exists() and not overwrite:
            raise CapabilityExistsError(spec.id)
        path.write_text(
            yaml.safe_dump(
                spec.model_dump(mode="json"),
                sort_keys=False,
                default_flow_style=False,
            )
        )
        return spec

    def delete(self, capability_id: str) -> None:
        path = self.path_for(capability_id)
        if not path.exists():
            raise CapabilityNotFoundError(capability_id)
        path.unlink()


def default_data_dir() -> Path:
    """Return the directory used to persist capabilities and runs."""
    override = os.environ.get("FLYCHAIN_DATA_DIR")
    if override:
        return Path(override)
    return Path.home() / ".flychain" / "data"


def default_store() -> CapabilityStore:
    return CapabilityStore(default_data_dir() / "capabilities")


def slugify(value: str) -> str:
    s = value.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "capability"
