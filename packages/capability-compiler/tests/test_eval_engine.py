"""Tests for the auto-eval engine."""

from __future__ import annotations

import json

import pytest
from flychain_capability_compiler import (
    CapabilitySpec,
    DatasetSliceRule,
    EvalDimension,
    EvalEngine,
    PromotionGate,
    SliceMatcher,
    TraceData,
    aggregate_score,
    parse_judge_output,
    render_judge_prompt,
)
from flychain_capability_compiler.eval import (
    _GENERIC_JUDGE_TEMPLATE,  # type: ignore[attr-defined]
    split_system_user,
)


class FakeLLM:
    provider = "fake"
    model = "fake"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def complete(self, *, system: str, user: str, json_mode: bool = False) -> str:
        self.calls.append({"system": system, "user": user})
        if not self._responses:
            raise AssertionError("FakeLLM: no responses queued")
        return self._responses.pop(0)


def _example_spec() -> CapabilitySpec:
    return CapabilitySpec(
        id="groundedness",
        name="Groundedness",
        description="Answers must be supported by the provided context.",
        eval_dimensions=[
            EvalDimension(id="has_support", description="Every claim has support.", weight=1.0),
            EvalDimension(id="no_hallucination", description="No invented facts.", weight=0.5),
        ],
        slice_rules=[DatasetSliceRule(type="tag", value="task=rag")],
        promotion_gate=PromotionGate(threshold=0.05, max_other_regression=0.02),
    )


# -- slice matcher ---------------------------------------------------------


def test_slice_matcher_empty_rules_matches_any() -> None:
    matcher = SliceMatcher([])
    trace = TraceData(trace_id="t", project_id="p", input="hi", output="hi")
    assert matcher.matches(trace) is True


def test_slice_matcher_tag_eq() -> None:
    matcher = SliceMatcher([DatasetSliceRule(type="tag", value="task=rag")])
    hit = TraceData(trace_id="t", project_id="p", input="i", output="o", tags={"task": "rag"})
    miss = TraceData(trace_id="t", project_id="p", input="i", output="o", tags={"task": "chat"})
    no_tags = TraceData(trace_id="t", project_id="p", input="i", output="o")
    assert matcher.matches(hit) is True
    assert matcher.matches(miss) is False
    assert matcher.matches(no_tags) is False


def test_slice_matcher_tag_presence() -> None:
    matcher = SliceMatcher([DatasetSliceRule(type="tag", value="task")])
    hit = TraceData(trace_id="t", project_id="p", input="i", output="o", tags={"task": "x"})
    miss = TraceData(trace_id="t", project_id="p", input="i", output="o", tags={"env": "x"})
    assert matcher.matches(hit) is True
    assert matcher.matches(miss) is False


def test_slice_matcher_regex() -> None:
    matcher = SliceMatcher([DatasetSliceRule(type="regex", value=r"page\s+\d+")])
    hit = TraceData(trace_id="t", project_id="p", input="Find page 12 please", output="...")
    miss = TraceData(trace_id="t", project_id="p", input="hello world", output="...")
    assert matcher.matches(hit) is True
    assert matcher.matches(miss) is False


def test_slice_matcher_negate() -> None:
    matcher = SliceMatcher([DatasetSliceRule(type="tag", value="task=rag", negate=True)])
    rag = TraceData(trace_id="t", project_id="p", input="i", output="o", tags={"task": "rag"})
    not_rag = TraceData(trace_id="t", project_id="p", input="i", output="o", tags={"task": "chat"})
    assert matcher.matches(rag) is False
    assert matcher.matches(not_rag) is True


def test_slice_matcher_only_semantic_rules_match_all() -> None:
    """In v1, a capability with only semantic rules applies to every trace."""
    matcher = SliceMatcher([DatasetSliceRule(type="semantic", value="RAG traffic")])
    trace = TraceData(trace_id="t", project_id="p", input="i", output="o")
    assert matcher.matches(trace) is True


def test_slice_matcher_semantic_does_not_widen_concrete_rules() -> None:
    """A semantic rule alongside a tag rule does not bypass the tag rule."""
    matcher = SliceMatcher(
        [
            DatasetSliceRule(type="tag", value="task=rag"),
            DatasetSliceRule(type="semantic", value="RAG traffic"),
        ]
    )
    in_scope = TraceData(trace_id="t", project_id="p", input="i", output="o", tags={"task": "rag"})
    out_of_scope = TraceData(
        trace_id="t", project_id="p", input="i", output="o", tags={"task": "chat"}
    )
    assert matcher.matches(in_scope) is True
    assert matcher.matches(out_of_scope) is False


# -- prompt rendering ------------------------------------------------------


def test_render_judge_prompt_substitutes_trace_fields() -> None:
    template = "input={{trace.input}} output={{ trace.output }} ctx={{trace.context}}"
    trace = TraceData(trace_id="t", project_id="p", input="IN", output="OUT", context="CTX")
    dim = EvalDimension(id="x", description="x")
    rendered = render_judge_prompt(template, trace, dim)
    assert rendered == "input=IN output=OUT ctx=CTX"


def test_generic_template_splits_on_section_markers() -> None:
    dim = EvalDimension(id="acc", description="accuracy")
    trace = TraceData(trace_id="t", project_id="p", input="hi", output="hi")
    rendered = render_judge_prompt(_GENERIC_JUDGE_TEMPLATE, trace, dim)
    system, user = split_system_user(rendered)
    assert "strict evaluator" in system
    assert "Prompt: hi" in user


# -- judge output parsing --------------------------------------------------


def test_parse_judge_output_clamps_score() -> None:
    v = parse_judge_output(json.dumps({"score": 1.2, "passed": True, "reason": "r"}))
    assert v.score == 1.0
    v2 = parse_judge_output(json.dumps({"score": -0.5, "passed": False, "reason": "r"}))
    assert v2.score == 0.0


def test_parse_judge_output_infers_passed() -> None:
    v = parse_judge_output(json.dumps({"score": 0.8, "reason": "great"}))
    assert v.passed is True


def test_parse_judge_output_tolerates_prose() -> None:
    raw = 'Sure! {"score": 0.5, "passed": false, "reason": "meh"}\nEND'
    v = parse_judge_output(raw)
    assert v.score == 0.5
    assert v.passed is False


# -- engine ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_trace_runs_each_dimension() -> None:
    spec = _example_spec()
    fake = FakeLLM(
        [
            json.dumps({"score": 0.9, "passed": True, "reason": "ok"}),
            json.dumps({"score": 0.6, "passed": False, "reason": "missing cite"}),
        ]
    )
    engine = EvalEngine(llm=fake)
    trace = TraceData(
        trace_id="t1",
        project_id="p1",
        input="q",
        output="a",
        tags={"task": "rag"},
    )
    scores = await engine.evaluate_trace(trace, spec)
    assert len(scores) == 2
    ids = [s.dimension for s in scores]
    assert ids == ["has_support", "no_hallucination"]
    assert scores[0].score == 0.9
    assert scores[1].passed is False
    assert scores[0].judge_model == "fake:fake"


@pytest.mark.asyncio
async def test_evaluate_trace_respects_slice_rules() -> None:
    spec = _example_spec()  # requires task=rag tag
    fake = FakeLLM(["never-consumed"])
    engine = EvalEngine(llm=fake)
    trace = TraceData(trace_id="t", project_id="p", input="q", output="a", tags={"task": "chat"})
    scores = await engine.evaluate_trace(trace, spec)
    assert scores == []
    assert fake.calls == []


@pytest.mark.asyncio
async def test_engine_handles_bad_judge_output() -> None:
    spec = CapabilitySpec(
        id="x",
        name="x",
        description="x",
        eval_dimensions=[EvalDimension(id="d1", description="d1")],
    )
    fake = FakeLLM(["not json at all"])
    engine = EvalEngine(llm=fake)
    trace = TraceData(trace_id="t", project_id="p", input="q", output="a")
    scores = await engine.evaluate_trace(trace, spec)
    assert len(scores) == 1
    assert scores[0].score == 0.0
    assert scores[0].passed is False
    assert "judge parse error" in scores[0].reason


def test_aggregate_score_weighted_mean() -> None:
    spec = _example_spec()
    scores = [
        # has_support weight=1.0, score=0.9
        # no_hallucination weight=0.5, score=0.4
        # weighted mean = (0.9 * 1.0 + 0.4 * 0.5) / 1.5 = 1.1 / 1.5 ≈ 0.7333
        _score("groundedness", "has_support", 0.9),
        _score("groundedness", "no_hallucination", 0.4),
    ]
    agg = aggregate_score(scores, spec)
    assert abs(agg - (1.1 / 1.5)) < 1e-6


def _score(cap_id: str, dim: str, val: float):
    from flychain_capability_compiler import EvalScore

    return EvalScore(
        trace_id="t",
        project_id="p",
        capability_id=cap_id,
        dimension=dim,
        score=val,
        passed=val >= 0.75,
        reason="",
        judge_model="fake:fake",
    )
