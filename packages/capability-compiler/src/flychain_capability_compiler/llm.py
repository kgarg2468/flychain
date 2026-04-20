"""LLM clients used by the Capability Spec Compiler.

The compiler runs against the local Ollama model by default, with optional
OpenAI / Anthropic fallbacks if the user has provided keys. All clients
share a single :class:`LLMClient` protocol so the compiler can swap between
them transparently.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

import httpx


class LLMClient(Protocol):
    provider: str
    model: str

    async def complete(self, *, system: str, user: str, json_mode: bool = False) -> str: ...


class OllamaClient:
    provider = "local-ollama"

    def __init__(self, base_url: str | None = None, model: str = "llama3.2:3b-instruct") -> None:
        self.base_url = (
            base_url or os.environ.get("FLYCHAIN_OLLAMA_URL", "http://localhost:11434")
        ).rstrip("/")
        self.model = model

    async def complete(self, *, system: str, user: str, json_mode: bool = False) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            payload["format"] = "json"
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
        msg = data.get("message", {}).get("content", "")
        if not isinstance(msg, str):
            raise RuntimeError(f"unexpected Ollama response: {data}")
        return msg


class OpenAIClient:
    provider = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com",
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY") or ""
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def complete(self, *, system: str, user: str, json_mode: bool = False) -> str:
        headers = {"content-type": "application/json", "authorization": f"Bearer {self.api_key}"}
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
            resp = await client.post(
                f"{self.base_url}/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]


class AnthropicClient:
    provider = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-3-5-haiku-latest",
        base_url: str = "https://api.anthropic.com",
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY") or ""
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def complete(self, *, system: str, user: str, json_mode: bool = False) -> str:
        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "max_tokens": 2048,
            "temperature": 0.1,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
            resp = await client.post(f"{self.base_url}/v1/messages", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        parts = data.get("content") or []
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        return text


def auto_client(
    *,
    prefer: str | None = None,
    ollama_url: str | None = None,
    ollama_model: str = "llama3.2:3b-instruct",
) -> LLMClient:
    """Pick an LLM client based on available keys.

    Precedence (when ``prefer`` is None):
      1. OpenAI if ``OPENAI_API_KEY`` is set.
      2. Anthropic if ``ANTHROPIC_API_KEY`` is set.
      3. Local Ollama.
    """
    preferred = (prefer or os.environ.get("FLYCHAIN_COMPILER_PROVIDER") or "").strip().lower()

    if preferred == "openai" or (preferred == "" and os.environ.get("OPENAI_API_KEY")):
        return OpenAIClient()
    if preferred == "anthropic" or (preferred == "" and os.environ.get("ANTHROPIC_API_KEY")):
        return AnthropicClient()
    return OllamaClient(base_url=ollama_url, model=ollama_model)


def parse_json_strict(text: str) -> dict[str, Any]:
    """Parse a JSON object out of model output, tolerating stray prose/fences.

    Models sometimes wrap the object in ``` ``` blocks or add a short preamble.
    We extract the first balanced ``{...}`` substring and parse that.
    """
    text = text.strip()
    if text.startswith("```"):
        # strip fenced block
        text = text.strip("`")
        text = text.split("\n", 1)[-1] if "\n" in text else text
    # Find first balanced JSON object.
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                candidate = text[start : i + 1]
                return json.loads(candidate)
    # Fallback: try direct parse.
    return json.loads(text)
