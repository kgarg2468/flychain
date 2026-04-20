"""Training backends for FlyChain recipes.

Each backend implements the :class:`TrainingBackend` protocol: given a
:class:`Recipe` + path to an input JSONL + output directory, it produces a
trained adapter under ``output_dir/`` and returns a :class:`TrainingArtifact`
describing what was produced.

V1 ships:

    * :class:`DryRunBackend` - produces a fake artifact without running a
      trainer. Used in CI and when no GPU is present, and as the fallback
      when the requested backend's binary is not installed.
    * :class:`MLXLMBackend` - shells out to ``mlx_lm.lora`` on Apple Silicon.
    * :class:`UnslothBackend` - shells out to ``python -m unsloth`` on CUDA.

A backend is selected per-recipe (the ``backend`` field) or auto-picked by
the orchestrator based on host detection.
"""

from __future__ import annotations

import json
import logging
import platform
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from flychain_capability_compiler.recipe import Recipe, RecipeBackend, RecipeMethod

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TrainingArtifact:
    backend: str
    adapter_dir: str
    logs_path: str
    hyperparams: dict
    base_model: str
    dataset_path: str
    dry_run: bool = False
    ollama_model_tag: str | None = None
    gguf_path: str | None = None
    duration_s: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


class TrainingBackend(Protocol):
    name: str
    available: bool

    def run(self, *, recipe: Recipe, dataset_path: Path, output_dir: Path) -> TrainingArtifact: ...


# ---------------------------------------------------------------------------
# Dry-run backend (always available)
# ---------------------------------------------------------------------------


class DryRunBackend:
    name = "dry-run"
    available = True

    def run(self, *, recipe: Recipe, dataset_path: Path, output_dir: Path) -> TrainingArtifact:
        t0 = time.perf_counter()
        output_dir.mkdir(parents=True, exist_ok=True)
        adapter_dir = output_dir / "adapter"
        adapter_dir.mkdir(exist_ok=True)
        logs_path = output_dir / "train.log"

        summary = {
            "status": "dry-run",
            "recipe_id": recipe.id,
            "base_model": recipe.base_model,
            "dataset_path": str(dataset_path),
            "hyperparams": recipe.hyperparams.model_dump(),
        }
        (adapter_dir / "adapter.json").write_text(json.dumps(summary, indent=2))
        logs_path.write_text(
            "\n".join(
                [
                    f"[dry-run] would train {recipe.base_model}",
                    f"[dry-run] dataset: {dataset_path}",
                    f"[dry-run] hyperparams: {summary['hyperparams']}",
                    "[dry-run] no GPU was used",
                    "",
                ]
            )
        )
        return TrainingArtifact(
            backend=self.name,
            adapter_dir=str(adapter_dir),
            logs_path=str(logs_path),
            hyperparams=summary["hyperparams"],
            base_model=recipe.base_model,
            dataset_path=str(dataset_path),
            dry_run=True,
            duration_s=time.perf_counter() - t0,
        )


# ---------------------------------------------------------------------------
# Real backends (subprocess shims)
# ---------------------------------------------------------------------------


def _module_importable(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _run_subprocess(cmd: list[str], log_file: Path) -> int:
    """Run ``cmd`` streaming stdout+stderr into ``log_file``. Returns rc."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("w") as lf:
        proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, text=True, bufsize=1)
        proc.wait()
        return int(proc.returncode or 0)


class MLXLMBackend:
    """Wraps ``mlx_lm.lora`` on Apple Silicon."""

    name = "mlx-lm"

    def __init__(self) -> None:
        self.available = platform.system() == "Darwin" and _module_importable("mlx_lm")

    def run(self, *, recipe: Recipe, dataset_path: Path, output_dir: Path) -> TrainingArtifact:
        if not self.available:
            raise RuntimeError("mlx-lm backend not available on this host")
        t0 = time.perf_counter()
        output_dir.mkdir(parents=True, exist_ok=True)
        adapter_dir = output_dir / "adapter"
        logs_path = output_dir / "train.log"

        hp = recipe.hyperparams
        module = "mlx_lm.dpo" if recipe.method == RecipeMethod.DPO else "mlx_lm.lora"
        cmd = [
            "python",
            "-m",
            module,
            "--model",
            recipe.base_model,
            "--train",
            "--data",
            str(dataset_path),
            "--adapter-path",
            str(adapter_dir),
            "--learning-rate",
            str(hp.learning_rate),
            "--batch-size",
            str(hp.batch_size),
            "--iters",
            str(hp.epochs * 100),
            "--seed",
            str(hp.seed),
        ]
        rc = _run_subprocess(cmd, logs_path)
        if rc != 0:
            raise RuntimeError(f"mlx-lm training failed (rc={rc}); see {logs_path}")

        return TrainingArtifact(
            backend=self.name,
            adapter_dir=str(adapter_dir),
            logs_path=str(logs_path),
            hyperparams=hp.model_dump(),
            base_model=recipe.base_model,
            dataset_path=str(dataset_path),
            duration_s=time.perf_counter() - t0,
        )


class UnslothBackend:
    """Wraps ``unsloth`` on CUDA Linux hosts via a driver script."""

    name = "unsloth"

    def __init__(self) -> None:
        self.available = (
            platform.system() == "Linux"
            and shutil.which("nvidia-smi") is not None
            and _module_importable("unsloth")
        )

    def run(self, *, recipe: Recipe, dataset_path: Path, output_dir: Path) -> TrainingArtifact:
        if not self.available:
            raise RuntimeError("unsloth backend not available on this host")
        t0 = time.perf_counter()
        output_dir.mkdir(parents=True, exist_ok=True)
        adapter_dir = output_dir / "adapter"
        logs_path = output_dir / "train.log"
        driver_template = (
            _UNSLOTH_DPO_DRIVER if recipe.method == RecipeMethod.DPO else _UNSLOTH_DRIVER
        )
        driver = driver_template.format(
            base_model=recipe.base_model,
            dataset_path=str(dataset_path),
            adapter_dir=str(adapter_dir),
            lora_r=recipe.hyperparams.lora_r,
            lora_alpha=recipe.hyperparams.lora_alpha,
            lora_dropout=recipe.hyperparams.lora_dropout,
            lr=recipe.hyperparams.learning_rate,
            epochs=recipe.hyperparams.epochs,
            batch_size=recipe.hyperparams.batch_size,
            max_seq_len=recipe.hyperparams.max_seq_len,
            seed=recipe.hyperparams.seed,
        )
        driver_path = output_dir / "unsloth_driver.py"
        driver_path.write_text(driver)
        cmd = ["python", str(driver_path)]
        rc = _run_subprocess(cmd, logs_path)
        if rc != 0:
            raise RuntimeError(f"unsloth training failed (rc={rc}); see {logs_path}")
        return TrainingArtifact(
            backend=self.name,
            adapter_dir=str(adapter_dir),
            logs_path=str(logs_path),
            hyperparams=recipe.hyperparams.model_dump(),
            base_model=recipe.base_model,
            dataset_path=str(dataset_path),
            duration_s=time.perf_counter() - t0,
        )


_UNSLOTH_DRIVER = '''\
"""Auto-generated unsloth SFT driver. Invoked by FlyChain."""
from __future__ import annotations

import json
from pathlib import Path

from unsloth import FastLanguageModel
from trl import SFTTrainer, SFTConfig
from datasets import load_dataset

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="{base_model}",
    max_seq_length={max_seq_len},
    load_in_4bit=True,
)
model = FastLanguageModel.get_peft_model(
    model,
    r={lora_r},
    lora_alpha={lora_alpha},
    lora_dropout={lora_dropout},
    target_modules=[
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ],
)

dataset = load_dataset("json", data_files="{dataset_path}", split="train")

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    dataset_text_field="completion",
    max_seq_length={max_seq_len},
    args=SFTConfig(
        output_dir="{adapter_dir}",
        learning_rate={lr},
        num_train_epochs={epochs},
        per_device_train_batch_size={batch_size},
        seed={seed},
        save_strategy="epoch",
    ),
)
trainer.train()
Path("{adapter_dir}").mkdir(parents=True, exist_ok=True)
model.save_pretrained("{adapter_dir}")
tokenizer.save_pretrained("{adapter_dir}")
summary = {{
    "status": "trained",
    "base_model": "{base_model}",
    "hyperparams": {{
        "lora_r": {lora_r},
        "lora_alpha": {lora_alpha},
        "lora_dropout": {lora_dropout},
    }},
}}
Path("{adapter_dir}/adapter.json").write_text(json.dumps(summary, indent=2))
'''


_UNSLOTH_DPO_DRIVER = '''\
"""Auto-generated unsloth DPO driver. Invoked by FlyChain."""
from __future__ import annotations

import json
from pathlib import Path

from unsloth import FastLanguageModel
from trl import DPOTrainer, DPOConfig
from datasets import load_dataset

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="{base_model}",
    max_seq_length={max_seq_len},
    load_in_4bit=True,
)
model = FastLanguageModel.get_peft_model(
    model,
    r={lora_r},
    lora_alpha={lora_alpha},
    lora_dropout={lora_dropout},
    target_modules=[
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ],
)

dataset = load_dataset("json", data_files="{dataset_path}", split="train")

trainer = DPOTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    args=DPOConfig(
        output_dir="{adapter_dir}",
        learning_rate={lr},
        num_train_epochs={epochs},
        per_device_train_batch_size={batch_size},
        seed={seed},
        save_strategy="epoch",
    ),
)
trainer.train()
Path("{adapter_dir}").mkdir(parents=True, exist_ok=True)
model.save_pretrained("{adapter_dir}")
tokenizer.save_pretrained("{adapter_dir}")
summary = {{
    "status": "trained",
    "method": "dpo",
    "base_model": "{base_model}",
    "hyperparams": {{
        "lora_r": {lora_r},
        "lora_alpha": {lora_alpha},
        "lora_dropout": {lora_dropout},
    }},
}}
Path("{adapter_dir}/adapter.json").write_text(json.dumps(summary, indent=2))
'''


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


_BACKENDS: dict[str, TrainingBackend] = {
    RecipeBackend.DRY_RUN.value: DryRunBackend(),
    RecipeBackend.MLX_LM.value: MLXLMBackend(),
    RecipeBackend.UNSLOTH.value: UnslothBackend(),
}


def get_backend(name: str) -> TrainingBackend:
    backend = _BACKENDS.get(name)
    if backend is None:
        raise KeyError(f"no such training backend: {name}")
    return backend


def select_backend(recipe_backend: str, *, allow_fallback: bool = True) -> TrainingBackend:
    """Pick a backend; fall back to ``dry-run`` when the requested one isn't available."""
    try:
        backend = get_backend(recipe_backend)
    except KeyError:
        if not allow_fallback:
            raise
        logger.warning("unknown backend %s; falling back to dry-run", recipe_backend)
        return _BACKENDS[RecipeBackend.DRY_RUN.value]

    if backend.available:
        return backend
    if not allow_fallback:
        raise RuntimeError(f"backend {recipe_backend} is unavailable on this host")
    logger.warning(
        "backend %s is unavailable on this host (darwin=%s, cuda=%s); falling back to dry-run",
        recipe_backend,
        platform.system() == "Darwin",
        shutil.which("nvidia-smi") is not None,
    )
    return _BACKENDS[RecipeBackend.DRY_RUN.value]


def auto_host_backend() -> str:
    """Return the preferred backend id for the current host (pre-recipe-override)."""
    if platform.system() == "Darwin":
        return RecipeBackend.MLX_LM.value
    if shutil.which("nvidia-smi") is not None:
        return RecipeBackend.UNSLOTH.value
    return RecipeBackend.DRY_RUN.value
