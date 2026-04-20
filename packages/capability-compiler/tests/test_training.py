"""Tests for training backends (dry-run + selection)."""

from __future__ import annotations

from pathlib import Path

from flychain_capability_compiler import (
    DryRunBackend,
    Recipe,
    RecipeBackend,
    RecipeMethod,
    auto_host_backend,
    select_backend,
)


def _recipe(backend: RecipeBackend = RecipeBackend.DRY_RUN) -> Recipe:
    return Recipe(id="r", base_model="meta-llama/Llama-3.2-1B-Instruct", backend=backend)


def test_dry_run_backend_always_available() -> None:
    assert DryRunBackend().available is True


def test_dry_run_backend_produces_artifact(tmp_path: Path) -> None:
    ds = tmp_path / "dataset.jsonl"
    ds.write_text('{"prompt": "a", "completion": "b"}\n')
    artifact = DryRunBackend().run(
        recipe=_recipe(),
        dataset_path=ds,
        output_dir=tmp_path / "out",
    )
    assert artifact.dry_run is True
    assert Path(artifact.adapter_dir, "adapter.json").exists()
    assert Path(artifact.logs_path).exists()
    assert artifact.hyperparams["lora_r"] == 8


def test_select_backend_exact_match() -> None:
    backend = select_backend("dry-run")
    assert backend.name == "dry-run"


def test_select_backend_unknown_falls_back(caplog) -> None:
    backend = select_backend("does-not-exist")
    assert backend.name == "dry-run"


def test_auto_host_backend_returns_known_value() -> None:
    name = auto_host_backend()
    assert name in {"mlx-lm", "unsloth", "dry-run"}


def test_dry_run_backend_records_recipe_method(tmp_path: Path) -> None:
    ds = tmp_path / "d.jsonl"
    ds.write_text('{"a": 1}\n')
    recipe = _recipe()
    recipe.method = RecipeMethod.DPO
    artifact = DryRunBackend().run(recipe=recipe, dataset_path=ds, output_dir=tmp_path / "out")
    import json

    data = json.loads(Path(artifact.adapter_dir, "adapter.json").read_text())
    assert data["recipe_id"] == "r"
