"""Tests for the CapabilitySpec schema."""

from __future__ import annotations

import pytest
from flychain_capability_compiler import (
    CapabilitySpec,
    DatasetSliceRule,
    EvalDimension,
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
