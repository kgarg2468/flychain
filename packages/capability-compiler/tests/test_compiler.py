"""Tests for the CapabilityCompiler with a stubbed LLM."""

from __future__ import annotations

import json

import pytest
from flychain_capability_compiler import (
    CapabilityCompiler,
    parse_json_strict,
)
from flychain_capability_compiler.compiler import _coerce_spec


class FakeLLM:
    provider = "fake"
    model = "fake"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def complete(self, *, system: str, user: str, json_mode: bool = False) -> str:
        self.calls.append({"system": system, "user": user, "json_mode": json_mode})
        if not self._responses:
            raise AssertionError("FakeLLM: no more responses queued")
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_propose_questions_parses_json() -> None:
    fake = FakeLLM(
        [
            json.dumps(
                {
                    "questions": [
                        {"id": "success_examples", "question": "Give an example of success."},
                        {"id": "failure_examples", "question": "Give an example of failure."},
                        {"id": "scope_tags", "question": "Which tags identify in-scope traffic?"},
                    ]
                }
            )
        ]
    )
    compiler = CapabilityCompiler(llm=fake)
    questions = await compiler.propose_questions("I want my model to cite page numbers")
    ids = [q.id for q in questions]
    assert ids == ["success_examples", "failure_examples", "scope_tags"]


@pytest.mark.asyncio
async def test_compile_produces_validated_spec() -> None:
    llm_payload = {
        "id": "citation-fidelity",
        "name": "Citation Fidelity",
        "description": "Cite the page numbers for every factual claim.",
        "eval_dimensions": [
            {
                "id": "has_citation",
                "description": "At least one citation per claim.",
                "weight": 1.0,
            },
            {"id": "valid_page_range", "description": "Cited page exists.", "weight": 0.75},
        ],
        "slice_rules": [{"type": "tag", "value": "task=citation"}],
        "eligible_methods": ["sft", "dpo"],
        "recipe_refs": ["sft-mlx-lora.yaml"],
        "promotion_gate": {"threshold": 0.05, "max_other_regression": 0.02},
    }
    fake = FakeLLM([json.dumps(llm_payload)])
    compiler = CapabilityCompiler(llm=fake)
    spec = await compiler.compile(
        "Cite the page numbers for every factual claim.",
        answers={"success_examples": "page 12 cited"},
    )
    assert spec.id == "citation-fidelity"
    assert len(spec.eval_dimensions) == 2
    assert spec.eligible_methods[0].value == "sft"
    assert spec.promotion_gate.threshold == 0.05


@pytest.mark.asyncio
async def test_compile_tolerates_fenced_json() -> None:
    raw = "```json\n" + json.dumps({"id": "x", "name": "x", "description": "x"}) + "\n```"
    fake = FakeLLM([raw])
    compiler = CapabilityCompiler(llm=fake)
    spec = await compiler.compile("hello")
    assert spec.id == "x"
    # Default eval dimension gets added.
    assert spec.eval_dimensions and spec.eval_dimensions[0].id == "meets_user_intent"


def test_coerce_spec_slugifies_id() -> None:
    spec = _coerce_spec({"name": "Some Weird Name!", "description": "x"})
    assert spec.id == "some-weird-name"


def test_parse_json_strict_handles_prose_prefix() -> None:
    text = 'Sure! Here is the JSON:\n{"foo": 1}\nhope this helps'
    assert parse_json_strict(text) == {"foo": 1}


def test_parse_json_strict_handles_nested_objects() -> None:
    text = '{"a": {"b": 2}, "c": [1, 2]}'
    assert parse_json_strict(text) == {"a": {"b": 2}, "c": [1, 2]}
