"""Tests for the auto-promote gate."""

from __future__ import annotations

from flychain_capability_compiler import GateDecision, apply_gate


def test_gate_promotes_when_target_beats_threshold() -> None:
    v = apply_gate(
        target_capability_id="groundedness",
        baseline={"groundedness": 0.60, "instruction-following": 0.80},
        candidate={"groundedness": 0.70, "instruction-following": 0.80},
        threshold=0.05,
        max_other_regression=0.02,
    )
    assert v.decision == GateDecision.PROMOTE
    assert v.target_delta == pytest_approx(0.10)
    assert v.regressions == []


def test_gate_archives_when_target_below_threshold() -> None:
    v = apply_gate(
        target_capability_id="groundedness",
        baseline={"groundedness": 0.60},
        candidate={"groundedness": 0.62},
        threshold=0.05,
        max_other_regression=0.02,
    )
    assert v.decision == GateDecision.ARCHIVE
    assert "threshold" in v.reason


def test_gate_archives_on_regression_beyond_tolerance() -> None:
    v = apply_gate(
        target_capability_id="groundedness",
        baseline={"groundedness": 0.60, "instruction-following": 0.80},
        candidate={"groundedness": 0.70, "instruction-following": 0.70},  # -0.10
        threshold=0.05,
        max_other_regression=0.05,
    )
    assert v.decision == GateDecision.ARCHIVE
    assert len(v.regressions) == 1
    assert v.regressions[0].capability_id == "instruction-following"


def test_gate_tolerates_small_regressions_under_max() -> None:
    v = apply_gate(
        target_capability_id="groundedness",
        baseline={"groundedness": 0.60, "instruction-following": 0.80},
        candidate={"groundedness": 0.75, "instruction-following": 0.79},  # -0.01
        threshold=0.05,
        max_other_regression=0.02,
    )
    assert v.decision == GateDecision.PROMOTE
    assert v.regressions == []


def test_gate_archives_when_target_missing() -> None:
    v = apply_gate(
        target_capability_id="groundedness",
        baseline={},
        candidate={"instruction-following": 0.9},
        threshold=0.05,
        max_other_regression=0.02,
    )
    assert v.decision == GateDecision.ARCHIVE
    assert "missing" in v.reason


def pytest_approx(expected: float, tol: float = 1e-6):  # tiny helper
    class _P:
        def __eq__(self, other: object) -> bool:
            return isinstance(other, int | float) and abs(other - expected) < tol

    return _P()
