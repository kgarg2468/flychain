"""Tests for recipe schema and loader."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from flychain_capability_compiler import (
    Recipe,
    RecipeBackend,
    RecipeMethod,
    list_recipes,
    load_recipe,
    recipe_by_id,
)
from flychain_capability_compiler.recipe import default_recipes_dir
from pydantic import ValidationError


def test_v1_recipes_shipped() -> None:
    recipes = list_recipes()
    ids = {r.id for r in recipes}
    assert {"sft-mlx-lora", "sft-unsloth-lora", "sft-mlx-lora-local-3b"} <= ids


def test_recipe_by_id() -> None:
    r = recipe_by_id("sft-mlx-lora")
    assert r.base_model == "meta-llama/Llama-3.2-3B-Instruct"
    assert r.method == RecipeMethod.SFT
    assert r.backend == RecipeBackend.MLX_LM
    assert r.promotion_threshold == 0.05


def test_public_local_mlx_recipe_uses_ungated_model() -> None:
    r = recipe_by_id("sft-mlx-lora-local-3b")
    assert r.base_model == "mlx-community/Llama-3.2-3B-Instruct-4bit"
    assert r.method == RecipeMethod.SFT
    assert r.backend == RecipeBackend.MLX_LM
    assert r.hyperparams.epochs == 3
    assert r.hyperparams.learning_rate == 2e-5
    assert r.hyperparams.batch_size == 1


def test_load_recipe_from_path(tmp_path) -> None:
    path = tmp_path / "test.yaml"
    path.write_text(
        textwrap.dedent(
            """\
            id: my-recipe
            base_model: meta-llama/Llama-3.2-1B-Instruct
            method: sft
            backend: dry-run
            hyperparams:
              lora_r: 4
              lora_alpha: 8
              epochs: 1
              batch_size: 1
            promotion_threshold: 0.03
            max_other_regression: 0.01
            """
        )
    )
    r = load_recipe(path)
    assert r.id == "my-recipe"
    assert r.hyperparams.lora_r == 4
    assert r.hyperparams.epochs == 1


def test_rejects_unknown_backend(tmp_path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("id: x\nbase_model: m\nbackend: invalid\n")
    with pytest.raises(ValidationError):
        load_recipe(path)


def test_recipe_defaults() -> None:
    r = Recipe(id="r", base_model="m")
    assert r.method == RecipeMethod.SFT
    assert r.backend == RecipeBackend.MLX_LM
    assert r.hyperparams.lora_r == 8


def test_default_recipes_dir_respects_env_override(monkeypatch, tmp_path: Path) -> None:
    custom = tmp_path / "recipes"
    custom.mkdir()
    monkeypatch.setenv("FLYCHAIN_RECIPES_DIR", str(custom))
    assert default_recipes_dir() == custom
