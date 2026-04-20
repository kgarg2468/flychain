"""Auto-eval engine.

Given a :class:`CapabilitySpec` and a trace (prompt + output + optional
context), run every eval dimension through an LLM-as-judge and return
structured :class:`EvalScore` results.

The engine is deliberately backend-agnostic: it takes an ``LLMClient``
(local Ollama by default) and a directory of judge-prompt Markdown files
(``evals/judge-prompts`` at the repo root). Each eval dimension's
``judge_prompt_ref`` picks the template to use; if unset, a generic fallback
template is used.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from flychain_capability_compiler.llm import LLMClient, auto_client, parse_json_strict
from flychain_capability_compiler.schema import (
    CapabilitySpec,
    DatasetSliceRule,
    EvalDimension,
)

# -- data classes ----------------------------------------------------------


@dataclass(slots=True)
class TraceData:
    """Minimal representation of a trace for the eval engine."""

    trace_id: str
    project_id: str
    input: str
    output: str
    context: str = ""
    tags: dict[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tags"] = self.tags or {}
        return d


@dataclass(slots=True)
class JudgeVerdict:
    score: float
    passed: bool
    reason: str


@dataclass(slots=True)
class EvalScore:
    trace_id: str
    project_id: str
    capability_id: str
    dimension: str
    score: float
    passed: bool
    reason: str
    judge_model: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# -- slice matching --------------------------------------------------------


class SliceMatcher:
    """Decide whether a trace is in-scope for a capability's slice rules."""

    def __init__(self, rules: list[DatasetSliceRule]):
        self.rules = rules

    def matches(self, trace: TraceData) -> bool:
        # If no rules are specified, the capability applies to every trace.
        if not self.rules:
            return True

        # v1 policy:
        #   - "tag" and "regex" rules gate inclusion (any hit -> in-scope).
        #   - "semantic" rules are advisory / informational only; they don't
        #     affect matching until we ship a semantic classifier.
        #   - If the capability has *only* semantic rules, every trace is
        #     in-scope (the capability hasn't narrowed its traffic yet).
        concrete_rules = [r for r in self.rules if r.type != "semantic"]
        if not concrete_rules:
            return True

        for rule in concrete_rules:
            hit = self._match_rule(rule, trace)
            if rule.negate:
                hit = not hit
            if hit:
                return True
        return False

    @staticmethod
    def _match_rule(rule: DatasetSliceRule, trace: TraceData) -> bool:
        if rule.type == "tag":
            # Expected form: "k=v" or "k" (presence check).
            if "=" in rule.value:
                k, v = rule.value.split("=", 1)
                return (trace.tags or {}).get(k.strip()) == v.strip()
            return rule.value in (trace.tags or {})
        if rule.type == "regex":
            haystack = f"{trace.input}\n{trace.output}"
            try:
                return re.search(rule.value, haystack) is not None
            except re.error:
                return False
        # v1 semantic rules are advisory; higher-level ``matches`` handles
        # them specially so this fall-through only sees non-semantic types.
        return rule.type == "semantic"


# -- judge-prompt template rendering --------------------------------------


_GENERIC_JUDGE_TEMPLATE = """\
# Capability Eval - {dimension_id}

## System

You are a strict evaluator. Judge the model output on the following dimension:

  - {dimension_id}: {dimension_description}

Respond with strict JSON only:

```
{{ "score": <0.0-1.0>, "passed": <true|false>, "reason": "<short>" }}
```

`passed` is `true` iff `score >= 0.75`.

## User

Prompt: {{ trace.input }}
Context: {{ trace.context }}
Output: {{ trace.output }}
"""


_PLACEHOLDER_RE = re.compile(r"\{\{\s*trace\.(input|output|context)\s*\}\}")


def render_judge_prompt(template: str, trace: TraceData, dimension: EvalDimension) -> str:
    """Render ``{{ trace.* }}`` placeholders into the judge template."""

    def _replace(match: re.Match[str]) -> str:
        field = match.group(1)
        return getattr(trace, field, "") or ""

    rendered = _PLACEHOLDER_RE.sub(_replace, template)
    rendered = rendered.replace("{dimension_id}", dimension.id).replace(
        "{dimension_description}", dimension.description
    )
    return rendered


def split_system_user(prompt: str) -> tuple[str, str]:
    """Split a rendered prompt into (system, user) halves.

    We use ``## System`` / ``## User`` section markers in the shipped judge
    templates. If no markers are present the whole text becomes the user
    message.
    """
    if "## User" in prompt:
        parts = prompt.split("## User", 1)
        system_part = parts[0]
        user_part = parts[1]
        # Strip leading ``# Title`` and ``## System`` headers from system half.
        system_part = re.sub(r"^#[^\n]*\n", "", system_part)
        system_part = system_part.replace("## System", "").strip()
        user_part = user_part.strip()
        return system_part, user_part
    return "", prompt.strip()


def parse_judge_output(text: str) -> JudgeVerdict:
    data = parse_json_strict(text)
    score = float(data.get("score", 0.0) or 0.0)
    # Clamp to [0, 1].
    score = max(0.0, min(1.0, score))
    passed = data.get("passed")
    if passed is None:
        passed = score >= 0.75
    reason = str(data.get("reason", ""))
    return JudgeVerdict(score=score, passed=bool(passed), reason=reason)


# -- engine ----------------------------------------------------------------


def default_judge_prompts_dir() -> Path:
    """Return the repo-level ``evals/judge-prompts`` directory."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "evals" / "judge-prompts"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("evals/judge-prompts directory not found")


class EvalEngine:
    def __init__(
        self,
        *,
        llm: LLMClient | None = None,
        judge_prompts_dir: Path | None = None,
    ) -> None:
        self.llm = llm or auto_client()
        self.judge_prompts_dir = Path(judge_prompts_dir) if judge_prompts_dir else None
        self._template_cache: dict[str, str] = {}

    def _load_template(self, ref: str | None, dimension: EvalDimension) -> str:
        """Load a judge template by ref, falling back to the generic one."""
        if not ref:
            return _GENERIC_JUDGE_TEMPLATE
        if ref in self._template_cache:
            return self._template_cache[ref]
        dir_ = self.judge_prompts_dir
        if dir_ is None:
            try:
                dir_ = default_judge_prompts_dir()
            except FileNotFoundError:
                return _GENERIC_JUDGE_TEMPLATE
        path = dir_ / ref
        if not path.exists():
            return _GENERIC_JUDGE_TEMPLATE
        text = path.read_text()
        self._template_cache[ref] = text
        return text

    async def evaluate_dimension(
        self,
        trace: TraceData,
        capability: CapabilitySpec,
        dimension: EvalDimension,
    ) -> EvalScore:
        template = self._load_template(dimension.judge_prompt_ref, dimension)
        rendered = render_judge_prompt(template, trace, dimension)
        system, user = split_system_user(rendered)
        raw = await self.llm.complete(system=system, user=user, json_mode=True)
        try:
            verdict = parse_judge_output(raw)
        except Exception as exc:
            verdict = JudgeVerdict(
                score=0.0, passed=False, reason=f"judge parse error: {exc}: {raw[:200]}"
            )
        return EvalScore(
            trace_id=trace.trace_id,
            project_id=trace.project_id,
            capability_id=capability.id,
            dimension=dimension.id,
            score=verdict.score,
            passed=verdict.passed,
            reason=verdict.reason,
            judge_model=f"{self.llm.provider}:{self.llm.model}",
        )

    async def evaluate_trace(self, trace: TraceData, capability: CapabilitySpec) -> list[EvalScore]:
        matcher = SliceMatcher(capability.slice_rules)
        if not matcher.matches(trace):
            return []
        scores: list[EvalScore] = []
        for dim in capability.eval_dimensions:
            scores.append(await self.evaluate_dimension(trace, capability, dim))
        return scores

    async def evaluate_all(
        self, trace: TraceData, capabilities: list[CapabilitySpec]
    ) -> list[EvalScore]:
        out: list[EvalScore] = []
        for cap in capabilities:
            out.extend(await self.evaluate_trace(trace, cap))
        return out


def aggregate_score(scores: list[EvalScore], capability: CapabilitySpec) -> float:
    """Weighted-mean aggregate of per-dimension scores for a capability."""
    if not scores:
        return 0.0
    weights = {d.id: d.weight for d in capability.eval_dimensions}
    total_w = 0.0
    total = 0.0
    for s in scores:
        if s.capability_id != capability.id:
            continue
        w = float(weights.get(s.dimension, 1.0))
        total += s.score * w
        total_w += w
    return total / total_w if total_w > 0 else 0.0
