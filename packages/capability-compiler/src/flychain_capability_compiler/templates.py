"""Load capability templates from the repo.

Templates live in ``capabilities/templates/*.yaml``. Each one parses into a
populated :class:`CapabilitySpec`. This module is the authoritative source
of the 5-template library the dashboard's Recommended mode displays.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from flychain_capability_compiler.schema import CapabilitySpec


def default_templates_dir() -> Path:
    """Return the path to the shipped ``capabilities/templates`` directory."""
    override = os.environ.get("FLYCHAIN_TEMPLATES_DIR")
    if override:
        return Path(override)

    packaged = Path(__file__).resolve().parent / "_assets" / "templates"
    if packaged.is_dir():
        return packaged

    raise FileNotFoundError("packaged templates directory not found; set FLYCHAIN_TEMPLATES_DIR")


def load_template(path: str | Path) -> CapabilitySpec:
    """Load a single template file into a ``CapabilitySpec``."""
    data = yaml.safe_load(Path(path).read_text()) or {}
    return CapabilitySpec.model_validate(data)


def list_templates(templates_dir: str | Path | None = None) -> list[CapabilitySpec]:
    """Load every ``*.yaml`` template in the given (or default) directory."""
    directory = Path(templates_dir) if templates_dir else default_templates_dir()
    specs: list[CapabilitySpec] = []
    for yaml_path in sorted(directory.glob("*.yaml")):
        specs.append(load_template(yaml_path))
    return specs


def template_by_id(template_id: str, templates_dir: str | Path | None = None) -> CapabilitySpec:
    """Look up a single template by its ``id``."""
    for spec in list_templates(templates_dir):
        if spec.id == template_id:
            return spec
    raise KeyError(f"no template with id {template_id!r}")
