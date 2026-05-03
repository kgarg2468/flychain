"""Tests for the CapabilitySpec schema."""

from __future__ import annotations

import pytest
from flychain_capability_compiler import (
    CapabilitySpec,
    DatasetSliceRule,
    DeterministicEvaluator,
    DeterministicEvaluatorType,
    EvalDimension,
    EvaluatorConfig,
    EvaluatorMode,
    PromotionGate,
    TrainingMethod,
)
from pydantic import ValidationError


def _example_spec() -> CapabilitySpec:
    return CapabilitySpec(
        id="groundedness",
        name="Groundedness",
        description="Answers must be supported by the provided context.",
        eval_dimensions=[
            EvalDimension(id="has_support", description="Every claim has a source span."),
        ],
        slice_rules=[
            DatasetSliceRule(type="tag", value="rag=true"),
        ],
        eligible_methods=[TrainingMethod.SFT, TrainingMethod.DPO],
        recipe_refs=["sft-mlx-lora.yaml"],
        promotion_gate=PromotionGate(threshold=0.05, max_other_regression=0.02),
    )


def test_roundtrip_json() -> None:
    spec = _example_spec()
    data = spec.model_dump(mode="json")
    restored = CapabilitySpec.model_validate(data)
    assert restored == spec


def test_unknown_method_rejected() -> None:
    with pytest.raises(ValidationError):
        CapabilitySpec(
            id="x",
            name="x",
            description="x",
            eligible_methods=["orpo"],  # type: ignore[list-item]
        )


def test_promotion_gate_defaults() -> None:
    gate = PromotionGate()
    assert 0.0 <= gate.threshold <= 1.0
    assert 0.0 <= gate.max_other_regression <= 1.0


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        CapabilitySpec.model_validate(
            {
                "id": "x",
                "name": "x",
                "description": "x",
                "surprise_field": 1,
            }
        )


def test_eval_dimension_supports_deterministic_evaluator_roundtrip() -> None:
    spec = CapabilitySpec(
        id="adapter-sentinel",
        name="Adapter Sentinel",
        description="Return the exact adapter sentinel token.",
        eval_dimensions=[
            EvalDimension(
                id="exact_sentinel",
                description="Must return exactly ADAPTER_SENTINEL_OK.",
                evaluator=EvaluatorConfig(
                    mode=EvaluatorMode.DETERMINISTIC,
                    deterministic=DeterministicEvaluator(
                        type=DeterministicEvaluatorType.EXACT_MATCH,
                        expected="ADAPTER_SENTINEL_OK",
                        normalize={"trim": True},
                    ),
                ),
            )
        ],
    )

    restored = CapabilitySpec.model_validate(spec.model_dump(mode="json"))

    evaluator = restored.eval_dimensions[0].evaluator
    assert evaluator is not None
    assert evaluator.mode == EvaluatorMode.DETERMINISTIC
    assert evaluator.deterministic is not None
    assert evaluator.deterministic.type == DeterministicEvaluatorType.EXACT_MATCH
    assert evaluator.deterministic.expected == "ADAPTER_SENTINEL_OK"
    assert evaluator.deterministic.normalize.trim is True


def test_legacy_eval_dimension_defaults_to_llm_judge() -> None:
    dimension = EvalDimension(id="quality", description="Judge whether the answer is useful.")

    assert dimension.evaluator is None
