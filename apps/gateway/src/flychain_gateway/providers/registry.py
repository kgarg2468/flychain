"""Provider registry that routes a model id to a concrete provider adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from flychain_gateway.config import Settings
from flychain_gateway.models_registry import ModelRegistry
from flychain_gateway.providers.anthropic import AnthropicProvider
from flychain_gateway.providers.openai_compat import OpenAICompatibleProvider


@dataclass(slots=True)
class ResolvedProvider:
    provider_name: str
    model_id: str
    model_conf: dict[str, Any]
    adapter: Any
    api_key: str | None


class ProviderRouter:
    def __init__(
        self,
        *,
        settings: Settings,
        registry: ModelRegistry,
        http_timeout: httpx.Timeout | None = None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._timeout = http_timeout

        self._openai = OpenAICompatibleProvider(
            name="openai",
            base_url=registry.provider_base_url("openai") or settings.openai_base_url,
            timeout=self._timeout,
        )
        self._ollama = OpenAICompatibleProvider(
            name="local-ollama",
            base_url=registry.provider_base_url("local-ollama") or settings.ollama_url,
            timeout=self._timeout,
        )
        self._anthropic = AnthropicProvider(
            base_url=registry.provider_base_url("anthropic") or settings.anthropic_base_url,
            timeout=self._timeout,
        )

    def resolve_chat(self, model: str) -> ResolvedProvider:
        provider, conf = self._registry.resolve(model)
        if provider == "openai":
            return ResolvedProvider(
                provider_name=provider,
                model_id=conf.get("id", model),
                model_conf=conf,
                adapter=self._openai,
                api_key=self._settings.openai_api_key,
            )
        if provider == "local-ollama":
            return ResolvedProvider(
                provider_name=provider,
                model_id=conf.get("id", model),
                model_conf=conf,
                adapter=self._ollama,
                api_key=None,
            )
        if provider == "anthropic":
            # An Anthropic model was requested on the chat-completions route;
            # for Phase 1 we reject this - callers should use /v1/messages.
            raise ValueError(f"model '{model}' maps to Anthropic; use /v1/messages instead")
        raise ValueError(f"unsupported provider: {provider}")

    def resolve_messages(self, model: str) -> ResolvedProvider:
        provider, conf = self._registry.resolve(model)
        if provider != "anthropic":
            raise ValueError(
                f"model '{model}' maps to {provider}; use /v1/chat/completions instead"
            )
        return ResolvedProvider(
            provider_name=provider,
            model_id=conf.get("id", model),
            model_conf=conf,
            adapter=self._anthropic,
            api_key=self._settings.anthropic_api_key,
        )
