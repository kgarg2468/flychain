"""Pydantic schemas shared across the FlyChain gateway."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# OpenAI-compatible chat-completions schema (minimal, forwards unknown fields).
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: str | list[dict[str, Any]] | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    response_format: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Anthropic /v1/messages schema (minimal).
# ---------------------------------------------------------------------------


class AnthropicMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["user", "assistant"]
    content: str | list[dict[str, Any]]


class AnthropicMessagesRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[AnthropicMessage]
    system: str | list[dict[str, Any]] | None = None
    max_tokens: int = Field(default=1024, ge=1)
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    tools: list[dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Feedback endpoint schema.
# ---------------------------------------------------------------------------


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    project_id: str | None = None
    thumb: Literal["up", "down", "none"] | None = None
    score: int | None = Field(default=None, ge=-5, le=5)
    comment: str | None = None
    corrected_response: str | None = None


class FeedbackAccepted(BaseModel):
    feedback_id: str
    trace_id: str
    recorded: bool = True


# ---------------------------------------------------------------------------
# Trace objects (stored + returned).
# ---------------------------------------------------------------------------


class TraceRecord(BaseModel):
    trace_id: str
    project_id: str
    provider: str
    model: str
    method: str
    request: dict[str, Any]
    response: dict[str, Any] | None
    capability_ids: list[str] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    status: str = "ok"
    error: str = ""
    tags: dict[str, str] = Field(default_factory=dict)
