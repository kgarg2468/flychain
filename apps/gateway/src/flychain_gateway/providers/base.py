"""Provider base types + protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class ProviderResponse:
    payload: dict[str, Any]
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    raw_status: int = 200
    error: str = ""


class ChatProvider(Protocol):
    name: str

    async def chat_completions(
        self, *, model: str, body: dict[str, Any], api_key: str | None
    ) -> ProviderResponse: ...


class AnthropicProvider(Protocol):
    name: str

    async def messages(
        self, *, model: str, body: dict[str, Any], api_key: str | None
    ) -> ProviderResponse: ...
