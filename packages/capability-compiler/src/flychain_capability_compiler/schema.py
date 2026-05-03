"""Pydantic schema for a FlyChain ``CapabilitySpec``.

A ``CapabilitySpec`` is the structured, auditable output of the Capability
Spec Compiler. Every capability tracked in a FlyChain project is one of
these; the flywheel (auto-eval, clustering, dataset synthesis, training,
promotion gating) runs per capability.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TrainingMethod(StrEnum):
    SFT = "sft"
    DPO = "dpo"
    KTO = "kto"
    GRPO = "grpo"


class EvaluatorMode(StrEnum):
    LLM_JUDGE = "llm_judge"
    DETERMINISTIC = "deterministic"
    HYBRID = "hybrid"


class DeterministicEvaluatorType(StrEnum):
    EXACT_MATCH = "exact_match"
    CASE_INSENSITIVE_EXACT_MATCH = "case_insensitive_exact_match"
    CONTAINS = "contains"
    REGEX_MATCH = "regex_match"
    JSON_VALID = "json_valid"
    JSON_SCHEMA = "json_schema"
    NUMERIC_RANGE = "numeric_range"
    ONE_OF = "one_of"


class NormalizationRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trim: bool = False
    collapse_whitespace: bool = False


class DeterministicEvaluator(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: DeterministicEvaluatorType
    expected: Any | None = None
    pattern: str | None = None
    json_schema: dict[str, Any] | None = Field(default=None, alias="schema")
    min_value: float | None = Field(default=None, alias="min")
    max_value: float | None = Field(default=None, alias="max")
    options: list[Any] | None = None
    normalize: NormalizationRules = Field(default_factory=NormalizationRules)


class EvaluatorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: EvaluatorMode = EvaluatorMode.LLM_JUDGE
    deterministic: DeterministicEvaluator | None = None


class EvalDimension(BaseModel):
    """A single measurable dimension compiled out of the capability NL description."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Stable identifier, e.g. 'schema_valid'.")
    description: str = Field(..., description="Human-readable description of what is checked.")
    judge_prompt_ref: str | None = Field(
        default=None,
        description="Path (relative to evals/judge-prompts) of the judge template to use.",
    )
    evaluator: EvaluatorConfig | None = Field(
        default=None,
        description=(
            "Optional evaluator configuration. Missing means legacy LLM-as-judge behavior."
        ),
    )
    weight: float = Field(
        default=1.0,
        ge=0.0,
        le=10.0,
        description="Relative importance when aggregating per-capability scores.",
    )


class DatasetSliceRule(BaseModel):
    """A rule for deciding whether a trace is in-scope for this capability."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., description="One of: 'tag', 'regex', 'semantic'.")
    value: str = Field(..., description="Tag key=value, regex pattern, or NL description.")
    negate: bool = Field(default=False, description="Invert the rule if True.")


class PromotionGate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Minimum delta vs baseline (0-1) required to promote a candidate.",
    )
    max_other_regression: float = Field(
        default=0.02,
        ge=0.0,
        le=1.0,
        description=(
            "Maximum tolerated regression on any other tracked capability. Exceeding "
            "this archives the candidate regardless of target delta."
        ),
    )


class CapabilitySpec(BaseModel):
    """Structured capability spec. Serializable to YAML or JSON."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Slug identifier, e.g. 'groundedness'.")
    name: str = Field(..., description="Display name.")
    description: str = Field(..., description="Free-form natural-language description.")
    eval_dimensions: list[EvalDimension] = Field(default_factory=list)
    slice_rules: list[DatasetSliceRule] = Field(default_factory=list)
    eligible_methods: list[TrainingMethod] = Field(
        default_factory=lambda: [TrainingMethod.SFT],
        description="Training methods this capability is allowed to use.",
    )
    recipe_refs: list[str] = Field(
        default_factory=list,
        description="Paths (relative to recipes/) of recipes eligible for this capability.",
    )
    promotion_gate: PromotionGate = Field(default_factory=PromotionGate)
    metadata: dict[str, str] = Field(default_factory=dict)


__all__ = [
    "CapabilitySpec",
    "DatasetSliceRule",
    "DeterministicEvaluator",
    "DeterministicEvaluatorType",
    "EvalDimension",
    "EvaluatorConfig",
    "EvaluatorMode",
    "NormalizationRules",
    "PromotionGate",
    "TrainingMethod",
]
