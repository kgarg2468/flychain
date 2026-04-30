"""Tests for training backends (dry-run + selection)."""

from __future__ import annotations

import sys
from pathlib import Path

from flychain_capability_compiler import (
    DryRunBackend,
    MLXLMBackend,
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


def test_mlx_backend_stages_jsonl_data_dir_and_uses_current_python(
    monkeypatch, tmp_path: Path
) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    dataset_path.write_text('{"prompt": "say sentinel", "completion": "ADAPTER_SENTINEL_OK"}\n')
    calls: list[tuple[list[str], Path]] = []

    def fake_run_subprocess(cmd: list[str], log_file: Path) -> int:
        calls.append((cmd, log_file))
        return 0

    monkeypatch.setattr(
        "flychain_capability_compiler.training._run_subprocess",
        fake_run_subprocess,
    )

    recipe = Recipe(
        id="sft-mlx-lora-local-3b",
        base_model="mlx-community/Llama-3.2-3B-Instruct-4bit",
        backend=RecipeBackend.MLX_LM,
    )
    recipe.hyperparams.epochs = 1
    recipe.hyperparams.batch_size = 1

    backend = MLXLMBackend()
    backend.available = True
    artifact = backend.run(
        recipe=recipe,
        dataset_path=dataset_path,
        output_dir=tmp_path / "out",
    )

    assert calls, "training subprocess should be invoked"
    cmd, _log_file = calls[0]
    data_dir = Path(cmd[cmd.index("--data") + 1])
    assert cmd[0] == sys.executable
    assert "--mask-prompt" in cmd
    assert data_dir.is_dir()
    assert (data_dir / "train.jsonl").read_text() == dataset_path.read_text()
    assert artifact.backend == "mlx-lm"
    assert artifact.base_model == "mlx-community/Llama-3.2-3B-Instruct-4bit"
    assert artifact.dataset_path == str(dataset_path)
