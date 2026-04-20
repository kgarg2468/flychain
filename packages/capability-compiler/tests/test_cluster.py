"""Tests for failure clustering + dataset synthesis."""

from __future__ import annotations

import json

import numpy as np
import pytest
from flychain_capability_compiler import (
    CapabilitySpec,
    Cluster,
    EvalDimension,
    FailedTrace,
    HashEmbedder,
    cluster_failures,
    synthesize_dpo_dataset,
    synthesize_sft_dataset,
    write_jsonl,
)


def _spec() -> CapabilitySpec:
    return CapabilitySpec(
        id="cap-x",
        name="X",
        description="test capability",
        eval_dimensions=[EvalDimension(id="d1", description="d")],
    )


def _trace(tid: str, text: str, corrected: str | None = None) -> FailedTrace:
    return FailedTrace(
        trace_id=tid,
        project_id="p",
        input=f"explain {text}",
        output=f"bad answer about {text}",
        corrected_response=corrected,
    )


# -- clustering -----------------------------------------------------------


class FixedEmbedder:
    """Embedder returning a supplied matrix so we can control cluster shape."""

    provider = "fixed"
    model = "fixed"

    def __init__(self, matrix: np.ndarray) -> None:
        self._m = matrix

    async def embed(self, texts: list[str]) -> np.ndarray:
        assert len(texts) == self._m.shape[0]
        return self._m


class FakeLLM:
    provider = "fake"
    model = "fake"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def complete(self, *, system: str, user: str, json_mode: bool = False) -> str:
        self.calls.append({"system": system, "user": user, "json_mode": json_mode})
        if not self._responses:
            raise AssertionError("FakeLLM: out of responses")
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_cluster_failures_groups_nearby_embeddings() -> None:
    spec = _spec()
    # Three points tightly packed + three far away.
    near = np.random.default_rng(0).normal(loc=[1.0, 1.0], scale=0.01, size=(4, 2))
    far = np.random.default_rng(1).normal(loc=[-5.0, -5.0], scale=0.01, size=(4, 2))
    matrix = np.vstack([near, far]).astype(np.float32)
    failures = [_trace(f"n{i}", f"near{i}") for i in range(4)] + [
        _trace(f"f{i}", f"far{i}") for i in range(4)
    ]

    fake_llm = FakeLLM(
        [
            json.dumps({"label": "near cluster"}),
            json.dumps({"label": "far cluster"}),
        ]
    )
    result = await cluster_failures(
        capability=spec,
        failures=failures,
        embedder=FixedEmbedder(matrix),
        llm=fake_llm,
        min_cluster_size=3,
    )
    assert result.capability_id == spec.id
    assert len(result.clusters) == 2
    labels = sorted(c.label for c in result.clusters)
    assert labels == ["far cluster", "near cluster"]
    for cluster in result.clusters:
        assert len(cluster.trace_ids) == 4


@pytest.mark.asyncio
async def test_cluster_failures_handles_tiny_pool() -> None:
    spec = _spec()
    failures = [_trace("t1", "a"), _trace("t2", "b")]
    matrix = np.array([[0.0, 0.0], [0.1, 0.1]], dtype=np.float32)
    result = await cluster_failures(
        capability=spec,
        failures=failures,
        embedder=FixedEmbedder(matrix),
        llm=FakeLLM([]),
        min_cluster_size=3,
    )
    # Tiny pool -> single provisional cluster, no summarize call.
    assert len(result.clusters) == 1
    assert result.clusters[0].size == 2


@pytest.mark.asyncio
async def test_cluster_failures_empty_pool() -> None:
    spec = _spec()
    result = await cluster_failures(
        capability=spec,
        failures=[],
        embedder=HashEmbedder(),
        llm=FakeLLM([]),
    )
    assert result.clusters == []
    assert result.noise_trace_ids == []


# -- SFT synthesis --------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_sft_uses_corrected_responses() -> None:
    spec = _spec()
    failures = [
        _trace("t1", "a", corrected="ideal A"),
        _trace("t2", "b", corrected="ideal B"),
    ]
    cluster = Cluster(
        id="cap-x-c0", capability_id=spec.id, label="fix", size=2, trace_ids=["t1", "t2"]
    )
    rows = await synthesize_sft_dataset(
        capability=spec, cluster=cluster, failures=failures, generate_missing=False
    )
    assert len(rows) == 2
    assert rows[0]["completion"] == "ideal A"
    assert rows[1]["prompt"].startswith("explain b")
    assert rows[0]["capability_id"] == spec.id


@pytest.mark.asyncio
async def test_synthesize_sft_generates_missing_from_llm() -> None:
    spec = _spec()
    failures = [_trace("t1", "a"), _trace("t2", "b", corrected="ideal B")]
    cluster = Cluster(
        id="cap-x-c0", capability_id=spec.id, label="fix", size=2, trace_ids=["t1", "t2"]
    )
    fake = FakeLLM(["generated ideal A"])
    rows = await synthesize_sft_dataset(
        capability=spec, cluster=cluster, failures=failures, llm=fake
    )
    assert len(rows) == 2
    completions = {r["trace_id"]: r["completion"] for r in rows}
    assert completions["t1"] == "generated ideal A"
    assert completions["t2"] == "ideal B"


@pytest.mark.asyncio
async def test_synthesize_sft_skips_when_no_ideal() -> None:
    spec = _spec()
    failures = [_trace("t1", "a")]
    cluster = Cluster(id="cap-x-c0", capability_id=spec.id, label="fix", size=1, trace_ids=["t1"])
    # generate_missing=False and no corrected_response -> no rows.
    rows = await synthesize_sft_dataset(
        capability=spec, cluster=cluster, failures=failures, generate_missing=False
    )
    assert rows == []


# -- DPO synthesis --------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_dpo_uses_corrections_as_chosen() -> None:
    spec = _spec()
    failures = [_trace("t1", "a", corrected="ideal A")]
    cluster = Cluster(id="cap-x-c0", capability_id=spec.id, label="fix", size=1, trace_ids=["t1"])
    rows = await synthesize_dpo_dataset(
        capability=spec, cluster=cluster, failures=failures, generate_missing=False
    )
    assert len(rows) == 1
    assert rows[0]["chosen"] == "ideal A"
    assert rows[0]["rejected"].startswith("bad answer")
    assert rows[0]["prompt"].startswith("explain a")


@pytest.mark.asyncio
async def test_synthesize_dpo_skips_identical_chosen_rejected() -> None:
    spec = _spec()
    failures = [_trace("t1", "a", corrected="bad answer about a")]  # matches rejected
    cluster = Cluster(id="cap-x-c0", capability_id=spec.id, label="fix", size=1, trace_ids=["t1"])
    rows = await synthesize_dpo_dataset(
        capability=spec, cluster=cluster, failures=failures, generate_missing=False
    )
    assert rows == []


# -- persistence ----------------------------------------------------------


def test_write_jsonl_roundtrip(tmp_path) -> None:
    path = tmp_path / "sub" / "dataset.jsonl"
    rows = [{"a": 1}, {"b": "two"}, {"c": [1, 2, 3]}]
    write_jsonl(path, rows)
    lines = path.read_text().strip().splitlines()
    assert [json.loads(line) for line in lines] == rows
