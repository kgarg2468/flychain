"""Model registry: loads ``models.yaml`` and exposes provider lookup + cost calc.

Phase 1 uses this to resolve an incoming ``model`` string to a concrete
provider (openai | anthropic | local-ollama) and to compute USD cost per
request from prompt / completion token counts.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


class ModelNotFoundError(KeyError):
    pass


class ModelRegistry:
    def __init__(self, data: dict[str, Any]) -> None:
        self._providers: dict[str, dict[str, Any]] = data.get("providers", {})
        self._model_index: dict[str, tuple[str, dict[str, Any]]] = {}
        for provider, pconf in self._providers.items():
            for model in pconf.get("models", []) or []:
                mid = model.get("id")
                if mid:
                    self._model_index[mid] = (provider, model)
                    # Allow namespaced lookup like "openai:gpt-4o".
                    self._model_index[f"{provider}:{mid}"] = (provider, model)

    @classmethod
    def load(cls, path: str | Path | None = None) -> ModelRegistry:
        path = Path(path) if path else cls._default_path()
        raw = path.read_text()
        expanded = os.path.expandvars(raw)
        data = yaml.safe_load(expanded) or {}
        return cls(data)

    @staticmethod
    def _default_path() -> Path:
        override = os.environ.get("FLYCHAIN_MODELS_YAML")
        if override:
            return Path(override)
        packaged = Path(__file__).resolve().parent / "_assets" / "models.yaml"
        if packaged.exists():
            return packaged
        raise FileNotFoundError("packaged models.yaml not found; set FLYCHAIN_MODELS_YAML")

    def providers(self) -> list[str]:
        return list(self._providers.keys())

    def provider_base_url(self, provider: str) -> str | None:
        conf = self._providers.get(provider)
        if not conf:
            return None
        url = conf.get("base_url")
        if not isinstance(url, str):
            return url
        expanded = os.path.expandvars(url)
        # If any ``${VAR}`` placeholder remained after expansion, the env var
        # was not set; treat the URL as unresolved so callers fall back to
        # their own defaults (e.g. ``Settings.ollama_url``).
        if "${" in expanded:
            return None
        return expanded

    def resolve(self, model_id: str) -> tuple[str, dict[str, Any]]:
        if model_id in self._model_index:
            return self._model_index[model_id]
        raise ModelNotFoundError(model_id)

    def cost_usd(self, model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
        try:
            _, conf = self.resolve(model_id)
        except ModelNotFoundError:
            return 0.0
        prompt_rate = float(conf.get("prompt_usd_per_1k", 0.0))
        completion_rate = float(conf.get("completion_usd_per_1k", 0.0))
        return (prompt_tokens / 1000.0) * prompt_rate + (
            completion_tokens / 1000.0
        ) * completion_rate


@lru_cache(maxsize=1)
def get_registry() -> ModelRegistry:
    return ModelRegistry.load()
