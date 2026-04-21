"""Training recipe schema + loader.

A ``Recipe`` is a YAML file that describes how to turn a synthesized dataset
into a candidate adapter. It captures the base model, training method,
hyperparameters, backend, eval suite reference, and promotion threshold.

Recipes live in ``recipes/*.yaml`` and are forkable.
"""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class RecipeBackend(StrEnum):
    DRY_RUN = "dry-run"
    MLX_LM = "mlx-lm"
    UNSLOTH = "unsloth"
    AXOLOTL_MODAL = "axolotl-modal"
    SAGEMAKER = "sagemaker"
    TOGETHER = "together"


class RecipeMethod(StrEnum):
    SFT = "sft"
    DPO = "dpo"
    KTO = "kto"
    GRPO = "grpo"


class LoRAHyperparams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lora_r: int = Field(default=8, ge=1, le=256)
    lora_alpha: int = Field(default=16, ge=1, le=512)
    lora_dropout: float = Field(default=0.05, ge=0.0, le=0.9)
    learning_rate: float = Field(default=2e-4, gt=0.0)
    epochs: int = Field(default=3, ge=1, le=100)
    batch_size: int = Field(default=2, ge=1, le=256)
    max_seq_len: int = Field(default=2048, ge=64, le=32768)
    warmup_steps: int = Field(default=10, ge=0, le=10000)
    seed: int = Field(default=42)


class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Recipe slug, matches filename stem.")
    base_model: str = Field(..., description="HuggingFace repo id of the base model.")
    method: RecipeMethod = RecipeMethod.SFT
    backend: RecipeBackend = RecipeBackend.MLX_LM
    hyperparams: LoRAHyperparams = Field(default_factory=LoRAHyperparams)
    eval_suite_ref: str | None = Field(
        default=None,
        description="Capability id whose eval dimensions gate promotion.",
    )
    promotion_threshold: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Minimum delta vs baseline on the eval suite required to promote.",
    )
    max_other_regression: float = Field(default=0.02, ge=0.0, le=1.0)
    description: str = ""


def default_recipes_dir() -> Path:
    override = os.environ.get("FLYCHAIN_RECIPES_DIR")
    if override:
        return Path(override)

    packaged = Path(__file__).resolve().parent / "_assets" / "recipes"
    if packaged.is_dir():
        return packaged

    raise FileNotFoundError("packaged recipes directory not found; set FLYCHAIN_RECIPES_DIR")


def load_recipe(path: str | Path) -> Recipe:
    data = yaml.safe_load(Path(path).read_text()) or {}
    return Recipe.model_validate(data)


def list_recipes(recipes_dir: str | Path | None = None) -> list[Recipe]:
    directory = Path(recipes_dir) if recipes_dir else default_recipes_dir()
    recipes: list[Recipe] = []
    for yaml_path in sorted(directory.glob("*.yaml")):
        recipes.append(load_recipe(yaml_path))
    return recipes


def recipe_by_id(recipe_id: str, recipes_dir: str | Path | None = None) -> Recipe:
    for recipe in list_recipes(recipes_dir):
        if recipe.id == recipe_id:
            return recipe
    raise KeyError(f"no recipe with id {recipe_id!r}")
