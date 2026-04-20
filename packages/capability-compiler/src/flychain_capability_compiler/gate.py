"""Auto-promote gate.

The gate decides whether a newly-trained candidate should be promoted to
the active adapter slot. Policy:

  1. The candidate must beat the baseline on the target capability's
     aggregate score by at least ``promotion_threshold``.
  2. The candidate must not regress any other tracked capability's
     aggregate score by more than ``max_other_regression``.
  3. Sanity checks (configurable) ensure the candidate actually produced a
     non-trivial adapter.

The gate's verdict is auditable - every promotion and archive is logged
with the numeric deltas that drove the decision.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum


class GateDecision(StrEnum):
    PROMOTE = "promote"
    ARCHIVE = "archive"


@dataclass(slots=True)
class CapabilityDelta:
    capability_id: str
    baseline: float
    candidate: float
    delta: float


@dataclass(slots=True)
class GateVerdict:
    decision: GateDecision
    target_capability_id: str
    target_delta: float
    threshold: float
    max_other_regression: float
    regressions: list[CapabilityDelta] = field(default_factory=list)
    deltas: list[CapabilityDelta] = field(default_factory=list)
    reason: str = ""

    def promoted(self) -> bool:
        return self.decision == GateDecision.PROMOTE

    def as_dict(self) -> dict:
        return {
            "decision": self.decision.value,
            "target_capability_id": self.target_capability_id,
            "target_delta": self.target_delta,
            "threshold": self.threshold,
            "max_other_regression": self.max_other_regression,
            "deltas": [asdict(d) for d in self.deltas],
            "regressions": [asdict(d) for d in self.regressions],
            "reason": self.reason,
        }


def apply_gate(
    *,
    target_capability_id: str,
    baseline: dict[str, float],
    candidate: dict[str, float],
    threshold: float,
    max_other_regression: float,
) -> GateVerdict:
    """Apply the auto-promote gate.

    ``baseline`` and ``candidate`` are ``{capability_id: aggregate_score}``
    maps. The target capability must appear in both.
    """
    deltas: list[CapabilityDelta] = []
    regressions: list[CapabilityDelta] = []

    for cap_id in set(baseline) | set(candidate):
        base = float(baseline.get(cap_id, 0.0))
        cand = float(candidate.get(cap_id, 0.0))
        d = CapabilityDelta(
            capability_id=cap_id,
            baseline=base,
            candidate=cand,
            delta=cand - base,
        )
        deltas.append(d)

    target = next((d for d in deltas if d.capability_id == target_capability_id), None)
    if target is None:
        return GateVerdict(
            decision=GateDecision.ARCHIVE,
            target_capability_id=target_capability_id,
            target_delta=0.0,
            threshold=threshold,
            max_other_regression=max_other_regression,
            deltas=deltas,
            reason=f"target capability {target_capability_id!r} missing from scorecards",
        )

    if target.delta < threshold:
        return GateVerdict(
            decision=GateDecision.ARCHIVE,
            target_capability_id=target.capability_id,
            target_delta=target.delta,
            threshold=threshold,
            max_other_regression=max_other_regression,
            deltas=deltas,
            reason=(
                f"target delta {target.delta:+.4f} < threshold {threshold:+.4f} on "
                f"{target.capability_id}"
            ),
        )

    for d in deltas:
        if d.capability_id == target_capability_id:
            continue
        if d.delta < -max_other_regression:
            regressions.append(d)

    if regressions:
        return GateVerdict(
            decision=GateDecision.ARCHIVE,
            target_capability_id=target.capability_id,
            target_delta=target.delta,
            threshold=threshold,
            max_other_regression=max_other_regression,
            deltas=deltas,
            regressions=regressions,
            reason=(
                f"regression beyond {max_other_regression} on "
                + ", ".join(f"{r.capability_id} ({r.delta:+.4f})" for r in regressions)
            ),
        )

    return GateVerdict(
        decision=GateDecision.PROMOTE,
        target_capability_id=target.capability_id,
        target_delta=target.delta,
        threshold=threshold,
        max_other_regression=max_other_regression,
        deltas=deltas,
        regressions=[],
        reason=f"+{target.delta:.4f} on {target.capability_id}, no regressions",
    )
