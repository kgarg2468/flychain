"""OpenAI-compatible provider adapter.

Used for:
    * openai (api.openai.com)
    * local-ollama (docker-compose Ollama OpenAI-compatible endpoint)

The adapter is transport-thin: it forwards the request body verbatim to the
upstream ``/v1/chat/completions`` endpoint, returns the parsed JSON response,
and extracts token usage for cost accounting.
"""

from __future__ import annotations

from typing import Any

import httpx

from flychain_gateway.providers.base import ProviderResponse

DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


class OpenAICompatibleProvider:
    def __init__(self, name: str, base_url: str, timeout: httpx.Timeout | None = None) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout or DEFAULT_TIMEOUT

    async def chat_completions(
        self,
        *,
        model: str,
        body: dict[str, Any],
        api_key: str | None,
    ) -> ProviderResponse:
        url = self._resolve_url("/v1/chat/completions")
        headers = {"content-type": "application/json"}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=body, headers=headers)

        if resp.status_code >= 400:
            return ProviderResponse(
                payload={"error": resp.text},
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                raw_status=resp.status_code,
                error=f"upstream_{resp.status_code}",
            )

        data = resp.json()
        usage = data.get("usage") or {}
        return ProviderResponse(
            payload=data,
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            total_tokens=int(usage.get("total_tokens", 0) or 0),
            raw_status=resp.status_code,
        )

    def _resolve_url(self, path: str) -> str:
        # If base_url already ends with /v1, avoid doubling the path prefix.
        base = self.base_url
        if base.endswith("/v1") and path.startswith("/v1"):
            return base + path[len("/v1") :]
        return base + path
