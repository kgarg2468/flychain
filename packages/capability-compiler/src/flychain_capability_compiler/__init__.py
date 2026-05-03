"""Capability Spec Compiler.

Public API:

    - :class:`CapabilitySpec` and its component types
    - :class:`CapabilityCompiler`: natural-language -> ``CapabilitySpec``
    - :func:`list_templates` / :func:`template_by_id`: shipped template library
    - LLM clients (:class:`OllamaClient`, :class:`OpenAIClient`, :class:`AnthropicClient`)
"""

from __future__ import annotations

from flychain_capability_compiler.cluster import (
    Cluster,
    ClusteringResult,
    FailedTrace,
    SynthesizedDataset,
    cluster_failures,
    synthesize_dpo_dataset,
    synthesize_sft_dataset,
    write_jsonl,
)
from flychain_capability_compiler.compiler import (
    CapabilityCompiler,
    InterviewQuestion,
    spec_to_json,
)
from flychain_capability_compiler.embeddings import (
    Embedder,
    HashEmbedder,
    OllamaEmbedder,
    auto_embedder,
)
from flychain_capability_compiler.eval import (
    EvalEngine,
    EvalScore,
    JudgeVerdict,
    SliceMatcher,
    TraceData,
    aggregate_score,
    default_judge_prompts_dir,
    evaluate_deterministic,
    parse_judge_output,
    render_judge_prompt,
)
from flychain_capability_compiler.gate import (
    CapabilityDelta,
    GateDecision,
    GateVerdict,
    apply_gate,
)
from flychain_capability_compiler.llm import (
    AnthropicClient,
    LLMClient,
    OllamaClient,
    OpenAIClient,
    auto_client,
    parse_json_strict,
)
from flychain_capability_compiler.recipe import (
    LoRAHyperparams,
    Recipe,
    RecipeBackend,
    RecipeMethod,
    default_recipes_dir,
    list_recipes,
    load_recipe,
    recipe_by_id,
)
from flychain_capability_compiler.schema import (
    CapabilitySpec,
    DatasetSliceRule,
    DeterministicEvaluator,
    DeterministicEvaluatorType,
    EvalDimension,
    EvaluatorConfig,
    EvaluatorMode,
    NormalizationRules,
    PromotionGate,
    TrainingMethod,
)
from flychain_capability_compiler.templates import (
    default_templates_dir,
    list_templates,
    load_template,
    template_by_id,
)
from flychain_capability_compiler.training import (
    DryRunBackend,
    MLXLMBackend,
    TrainingArtifact,
    TrainingBackend,
    UnslothBackend,
    auto_host_backend,
    get_backend,
    select_backend,
)

__version__ = "0.0.0"

__all__ = [
    "__version__",
    # schema
    "CapabilitySpec",
    "DatasetSliceRule",
    "DeterministicEvaluator",
    "DeterministicEvaluatorType",
    "EvalDimension",
    "EvaluatorConfig",
    "EvaluatorMode",
    "NormalizationRules",
    "PromotionGate",
    "TrainingMethod",
    # compiler
    "CapabilityCompiler",
    "InterviewQuestion",
    "spec_to_json",
    # embeddings
    "Embedder",
    "HashEmbedder",
    "OllamaEmbedder",
    "auto_embedder",
    # clustering + dataset synthesis
    "Cluster",
    "ClusteringResult",
    "FailedTrace",
    "SynthesizedDataset",
    "cluster_failures",
    "synthesize_dpo_dataset",
    "synthesize_sft_dataset",
    "write_jsonl",
    # recipes
    "LoRAHyperparams",
    "Recipe",
    "RecipeBackend",
    "RecipeMethod",
    "default_recipes_dir",
    "list_recipes",
    "load_recipe",
    "recipe_by_id",
    # training backends
    "TrainingArtifact",
    "TrainingBackend",
    "DryRunBackend",
    "MLXLMBackend",
    "UnslothBackend",
    "auto_host_backend",
    "get_backend",
    "select_backend",
    # promotion gate
    "CapabilityDelta",
    "GateDecision",
    "GateVerdict",
    "apply_gate",
    # eval
    "EvalEngine",
    "EvalScore",
    "JudgeVerdict",
    "SliceMatcher",
    "TraceData",
    "aggregate_score",
    "default_judge_prompts_dir",
    "evaluate_deterministic",
    "parse_judge_output",
    "render_judge_prompt",
    # templates
    "list_templates",
    "template_by_id",
    "load_template",
    "default_templates_dir",
    # llm
    "LLMClient",
    "OllamaClient",
    "OpenAIClient",
    "AnthropicClient",
    "auto_client",
    "parse_json_strict",
]
