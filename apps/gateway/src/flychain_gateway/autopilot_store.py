"""File-backed Phase 4 autopilot policy, audit, and adapter history stores."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ulid import ULID


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class AutopilotPolicy:
    capability_id: str
    enabled: bool = False
    min_corrected_failures: int = 3
    min_cluster_size: int = 3
    allowed_training_recipes: list[str] = field(
        default_factory=lambda: ["sft-mlx-lora-local-3b"]
    )
    auto_generate_corrections: bool = False
    allow_generated_corrections: bool = False
    auto_create_dataset: bool = True
    auto_start_training: bool = True
    auto_run_served_validation: bool = True
    auto_promote: bool = False
    require_promotion_approval: bool = True
    allow_dry_run_fallback: bool = False
    require_served_validation: bool = True
    max_training_runs_per_day: int = 1
    promotion_cooldown_seconds: int = 86400
    rollback_mode: str = "disable_current"
    version: int = 1
    updated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["updated_at"] = data["updated_at"] or _now_iso()
        return data


@dataclass(slots=True)
class AutopilotDecision:
    id: str
    capability_id: str
    trigger: str
    policy_version: int
    action: str
    outcome: str
    reasons: list[str] = field(default_factory=list)
    input_counts: dict[str, int] = field(default_factory=dict)
    target_id: str | None = None
    job_ids: list[str] = field(default_factory=list)
    approval_status: str | None = None
    approval_note: str | None = None
    approved_at: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class AutopilotStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.policies_dir = self.directory / "policies"
        self.audit_dir = self.directory / "audit"
        self.history_dir = self.directory / "adapter-history"
        self.policies_dir.mkdir(parents=True, exist_ok=True)
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def default_policy(self, capability_id: str, *, threshold: int) -> AutopilotPolicy:
        return AutopilotPolicy(
            capability_id=capability_id,
            min_corrected_failures=threshold,
            min_cluster_size=threshold,
            updated_at=_now_iso(),
        )

    def _policy_path(self, capability_id: str) -> Path:
        return self.policies_dir / f"{capability_id}.json"

    def load_policy(self, capability_id: str, *, threshold: int) -> AutopilotPolicy:
        policy = self.default_policy(capability_id, threshold=threshold)
        path = self._policy_path(capability_id)
        if not path.exists():
            return policy
        data = json.loads(path.read_text())
        for key, value in data.items():
            if hasattr(policy, key):
                setattr(policy, key, value)
        if not policy.min_corrected_failures:
            policy.min_corrected_failures = threshold
        if not policy.min_cluster_size:
            policy.min_cluster_size = threshold
        return policy

    def save_policy(
        self,
        capability_id: str,
        *,
        threshold: int,
        patch: dict[str, Any],
    ) -> AutopilotPolicy:
        policy = self.load_policy(capability_id, threshold=threshold)
        for key, value in patch.items():
            if value is not None and hasattr(policy, key) and key not in {"capability_id", "version"}:
                setattr(policy, key, value)
        policy.version += 1
        policy.updated_at = _now_iso()
        self._policy_path(capability_id).write_text(json.dumps(policy.as_dict(), indent=2))
        return policy

    def _audit_path(self, capability_id: str) -> Path:
        path = self.audit_dir / capability_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def append_decision(
        self,
        capability_id: str,
        *,
        trigger: str,
        policy_version: int,
        action: str,
        outcome: str,
        reasons: list[str] | None = None,
        input_counts: dict[str, int] | None = None,
        target_id: str | None = None,
        job_ids: list[str] | None = None,
        approval_status: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> AutopilotDecision:
        decision = AutopilotDecision(
            id=f"auto_{ULID()}",
            capability_id=capability_id,
            trigger=trigger,
            policy_version=policy_version,
            action=action,
            outcome=outcome,
            reasons=list(reasons or []),
            input_counts=dict(input_counts or {}),
            target_id=target_id,
            job_ids=list(job_ids or []),
            approval_status=approval_status,
            result=dict(result or {}),
        )
        self.save_decision(decision)
        return decision

    def save_decision(self, decision: AutopilotDecision) -> None:
        decision.updated_at = _now_iso()
        path = self._audit_path(decision.capability_id) / f"{decision.id}.json"
        path.write_text(json.dumps(decision.as_dict(), indent=2))

    def load_decision(self, capability_id: str, decision_id: str) -> AutopilotDecision | None:
        path = self._audit_path(capability_id) / f"{decision_id}.json"
        if not path.exists():
            return None
        return AutopilotDecision(**json.loads(path.read_text()))

    def list_audit(self, capability_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = [
            json.loads(path.read_text())
            for path in self._audit_path(capability_id).glob("*.json")
        ]
        rows.sort(key=lambda row: row.get("created_at", ""), reverse=True)
        return rows[:limit]

    def record_adapter_history(
        self,
        capability_id: str,
        *,
        previous: dict[str, Any] | None,
        current: dict[str, Any] | None,
        reason: str,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        row = {
            "id": f"hist_{ULID()}",
            "capability_id": capability_id,
            "previous": previous,
            "current": current,
            "reason": reason,
            "decision_id": decision_id,
            "ts": _now_iso(),
        }
        path = self.history_dir / f"{capability_id}.json"
        history = json.loads(path.read_text()) if path.exists() else []
        history.append(row)
        path.write_text(json.dumps(history, indent=2))
        return row

    def previous_adapter(self, capability_id: str) -> dict[str, Any] | None:
        path = self.history_dir / f"{capability_id}.json"
        if not path.exists():
            return None
        history = json.loads(path.read_text())
        for row in reversed(history):
            previous = row.get("previous")
            current = row.get("current")
            if previous and previous != current:
                return previous
        return None
