"""Capability Spec Compiler.

Turns a natural-language capability description into a populated
:class:`CapabilitySpec`. Flow:

1. ``propose_questions(description)`` asks an LLM for 3-6 clarifying questions.
2. User answers those questions (free text).
3. ``compile(description, answers)`` asks the LLM to emit a structured spec,
   then validates the output against the schema.

The interview + compile phases each make **one** LLM call; both honor
``json_mode`` so local Ollama, OpenAI, and Anthropic all return
machine-parseable output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from flychain_capability_compiler.llm import LLMClient, auto_client, parse_json_strict
from flychain_capability_compiler.schema import (
    CapabilitySpec,
    DatasetSliceRule,
    EvalDimension,
    PromotionGate,
    TrainingMethod,
)

# -- prompts ---------------------------------------------------------------

_QUESTIONS_SYSTEM = """\
You are the Capability Spec interviewer for FlyChain. The user has described
a capability they want to improve in their model. Ask 3-6 sharp, non-redundant
questions that will let you compile a structured CapabilitySpec. Focus on:

  - what counts as a success vs a failure (concrete examples the user can give)
  - which traffic is in-scope (tags, request shapes, semantic filters)
  - acceptable latency / cost / length budgets
  - whether prefernce data is already available (thumbs / corrections)
  - how much delta vs baseline the user would need before promoting

Reply with strict JSON only, matching:

  { "questions": [ { "id": "<snake_case>", "question": "<text>" }, ... ] }
"""

_COMPILE_SYSTEM = """\
You are the Capability Spec compiler for FlyChain. Given (1) a user's natural
language description of a capability and (2) the user's answers to the
interviewer's clarifying questions, emit a CapabilitySpec as strict JSON.

Schema (omit fields only if you truly have no info):

{
  "id": "<kebab-case-slug>",
  "name": "<display name>",
  "description": "<1-3 sentences>",
  "eval_dimensions": [
    { "id": "<snake_case>", "description": "<what the judge checks>", "weight": <0.0-2.0> }
  ],
  "slice_rules": [
    { "type": "tag" | "regex" | "semantic", "value": "<filter>" }
  ],
  "eligible_methods": ["sft", "dpo"],
  "recipe_refs": ["sft-mlx-lora.yaml"],
  "promotion_gate": { "threshold": 0.05, "max_other_regression": 0.02 }
}

Rules:
  - Always produce at least one eval dimension.
  - Use `semantic` slice rules only when tag/regex can't express the filter.
  - Default eligible_methods to ["sft", "dpo"] unless the user says otherwise.
  - Keep `id` globally unique and kebab-case.
  - Keep `eval_dimensions[*].id` snake_case and stable.
  - Output valid JSON only, no prose, no markdown.
"""


# -- data classes ----------------------------------------------------------


@dataclass(slots=True)
class InterviewQuestion:
    id: str
    question: str


# -- compiler --------------------------------------------------------------


class CapabilityCompiler:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or auto_client()

    async def propose_questions(self, description: str) -> list[InterviewQuestion]:
        """Ask the LLM for 3-6 clarifying questions."""
        raw = await self.llm.complete(
            system=_QUESTIONS_SYSTEM,
            user=f"Capability description:\n\n{description}",
            json_mode=True,
        )
        data = parse_json_strict(raw)
        questions_raw = data.get("questions", [])
        return [
            InterviewQuestion(id=str(q["id"]), question=str(q["question"]))
            for q in questions_raw
            if isinstance(q, dict) and "id" in q and "question" in q
        ]

    async def compile(
        self,
        description: str,
        answers: dict[str, str] | None = None,
    ) -> CapabilitySpec:
        """Compile a full spec from a description + optional interview answers."""
        prompt_parts: list[str] = [f"Description:\n{description.strip()}"]
        if answers:
            answer_lines = "\n".join(f"- {qid}: {ans}" for qid, ans in answers.items())
            prompt_parts.append(f"Interview answers:\n{answer_lines}")
        raw = await self.llm.complete(
            system=_COMPILE_SYSTEM,
            user="\n\n".join(prompt_parts),
            json_mode=True,
        )
        data = parse_json_strict(raw)
        return _coerce_spec(data)


def _coerce_spec(data: dict) -> CapabilitySpec:
    """Take loose LLM JSON and coerce it to a validated ``CapabilitySpec``."""
    eval_dims_in = data.get("eval_dimensions") or []
    eval_dims: list[EvalDimension] = []
    for d in eval_dims_in:
        if not isinstance(d, dict) or "id" not in d:
            continue
        eval_dims.append(
            EvalDimension(
                id=str(d["id"]),
                description=str(d.get("description", "")),
                judge_prompt_ref=_coerce_optional_str(d.get("judge_prompt_ref")),
                weight=_coerce_float(d.get("weight"), default=1.0),
            )
        )

    slice_rules_in = data.get("slice_rules") or []
    slice_rules: list[DatasetSliceRule] = []
    for s in slice_rules_in:
        if not isinstance(s, dict) or "type" not in s or "value" not in s:
            continue
        t = str(s["type"]).lower()
        if t not in {"tag", "regex", "semantic"}:
            continue
        slice_rules.append(
            DatasetSliceRule(type=t, value=str(s["value"]), negate=bool(s.get("negate", False)))
        )

    methods_in = data.get("eligible_methods") or ["sft"]
    methods: list[TrainingMethod] = []
    for m in methods_in:
        try:
            methods.append(TrainingMethod(str(m).lower()))
        except ValueError:
            continue
    if not methods:
        methods = [TrainingMethod.SFT]

    gate_in = data.get("promotion_gate") or {}
    gate = PromotionGate(
        threshold=_coerce_float(gate_in.get("threshold"), default=0.05),
        max_other_regression=_coerce_float(gate_in.get("max_other_regression"), default=0.02),
    )

    return CapabilitySpec(
        id=_coerce_slug(data.get("id") or data.get("name") or "capability"),
        name=str(data.get("name") or data.get("id") or "Capability"),
        description=str(data.get("description") or ""),
        eval_dimensions=eval_dims
        or [
            EvalDimension(
                id="meets_user_intent",
                description="Output meets the user's intent as described.",
                weight=1.0,
            )
        ],
        slice_rules=slice_rules,
        eligible_methods=methods,
        recipe_refs=[str(x) for x in (data.get("recipe_refs") or [])],
        promotion_gate=gate,
        metadata={k: str(v) for k, v in (data.get("metadata") or {}).items()},
    )


def _coerce_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_optional_str(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    return None


def _coerce_slug(value: str) -> str:
    import re

    slug = value.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug or "capability"


def spec_to_json(spec: CapabilitySpec) -> str:
    return json.dumps(spec.model_dump(mode="json"), indent=2)
