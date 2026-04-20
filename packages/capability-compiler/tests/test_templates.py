"""Tests for loading capability templates from capabilities/templates."""

from __future__ import annotations

from flychain_capability_compiler import list_templates, template_by_id


def test_v1_templates_present() -> None:
    ids = {spec.id for spec in list_templates()}
    assert ids == {
        "groundedness",
        "instruction-following",
        "code-correctness",
        "uncertainty-calibration",
        "multi-step-reasoning",
    }


def test_each_template_has_at_least_one_dimension() -> None:
    for spec in list_templates():
        assert spec.eval_dimensions, f"{spec.id} has no eval dimensions"
        for dim in spec.eval_dimensions:
            assert dim.id
            assert dim.description


def test_each_template_has_slice_rules_and_gate() -> None:
    for spec in list_templates():
        assert spec.slice_rules, f"{spec.id} has no slice rules"
        gate = spec.promotion_gate
        assert 0.0 <= gate.threshold <= 1.0
        assert 0.0 <= gate.max_other_regression <= 1.0


def test_template_by_id_lookup() -> None:
    g = template_by_id("groundedness")
    assert g.name == "Groundedness"
    assert any(d.id == "all_claims_supported" for d in g.eval_dimensions)
