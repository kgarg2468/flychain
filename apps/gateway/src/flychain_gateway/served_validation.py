"""Validation proof checks for served adapter promotion."""

from __future__ import annotations

from typing import Any

from flychain_gateway.training_store import TrainingRun


def served_validation_errors(run: TrainingRun) -> list[str]:
    """Return reasons a real adapter run is not safe to activate."""
    artifact = run.artifact or {}
    if artifact.get("backend") != "mlx-lm" or bool(artifact.get("dry_run")):
        return []

    validation: dict[str, Any] = dict(run.served_validation or {})
    errors: list[str] = []
    if validation.get("status") != "passed":
        errors.append("served validation has not passed")
    if validation.get("timed_out") is True or validation.get("status") == "timed_out":
        errors.append("served validation timed out")
    if not validation.get("validation_trace_ids"):
        errors.append("served validation has no validation traces")
    if validation.get("provider") != "local-mlx":
        errors.append("served validation used wrong provider")
    if validation.get("adapter_run_id") != run.id:
        errors.append("wrong adapter run id")
    if validation.get("adapter_capability_id") != run.capability_id:
        errors.append("wrong adapter capability id")
    if validation.get("routing_mode") != "candidate":
        errors.append("served validation did not use candidate routing")
    if validation.get("failures"):
        errors.append("served validation has failures")
    return errors


def has_passed_served_validation(run: TrainingRun) -> bool:
    return not served_validation_errors(run)
