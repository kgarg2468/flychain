"""Anthropic /v1/messages provider adapter."""

from __future__ import annotations

from typing import Any

import httpx

from flychain_gateway.providers.base import ProviderResponse

DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
_DEFAULT_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        base_url: str = "https://api.anthropic.com",
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout or DEFAULT_TIMEOUT

    async def messages(
        self,
        *,
        model: str,
        body: dict[str, Any],
        api_key: str | None,
    ) -> ProviderResponse:
        url = f"{self.base_url}/v1/messages"
        headers = {
            "content-type": "application/json",
            "anthropic-version": _DEFAULT_ANTHROPIC_VERSION,
        }
        if api_key:
            headers["x-api-key"] = api_key

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
        prompt = int(usage.get("input_tokens", 0) or 0)
        completion = int(usage.get("output_tokens", 0) or 0)
        return ProviderResponse(
            payload=data,
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            raw_status=resp.status_code,
        )
