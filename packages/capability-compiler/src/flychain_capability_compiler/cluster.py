"""Failure clustering + dataset synthesis.

Takes a pool of failed traces for a capability, embeds them, clusters with
HDBSCAN, labels each cluster with a one-shot LLM summary, and emits
training datasets (SFT + optionally DPO).

This is the step where repeated failures become targeted training data -
the center of gravity of the FlyChain flywheel.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.cluster import HDBSCAN

from flychain_capability_compiler.embeddings import Embedder, auto_embedder
from flychain_capability_compiler.llm import LLMClient, auto_client
from flychain_capability_compiler.schema import CapabilitySpec

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FailedTrace:
    """Input row for the clustering pipeline."""

    trace_id: str
    project_id: str
    input: str
    output: str
    context: str = ""
    corrected_response: str | None = None
    tags: dict[str, str] = field(default_factory=dict)

    def signature(self) -> str:
        """Text used for embedding + clustering."""
        parts = [
            ("PROMPT", _normalize_text(self.input)),
            ("OUTPUT", _normalize_text(self.output)),
        ]
        if self.context.strip():
            parts.append(("CONTEXT", _normalize_text(self.context)))
        if self.corrected_response and self.corrected_response.strip():
            parts.append(("IDEAL", _normalize_text(self.corrected_response)))
        return "\n\n".join(f"{label}:\n{text}" for label, text in parts if text)


@dataclass(slots=True)
class Cluster:
    id: str
    capability_id: str
    label: str
    size: int
    trace_ids: list[str]


@dataclass(slots=True)
class ClusteringResult:
    capability_id: str
    clusters: list[Cluster]
    noise_trace_ids: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "clusters": [asdict(c) for c in self.clusters],
            "noise_trace_ids": list(self.noise_trace_ids),
        }


@dataclass(slots=True)
class SynthesizedDataset:
    id: str
    capability_id: str
    cluster_id: str | None
    method: str  # "sft" or "dpo"
    path: str
    row_count: int


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


def _cluster_label(trace_ids: list[str], label: str) -> str:
    # Stable label prefix combining the user-facing label with size hint.
    return label


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


async def cluster_failures(
    *,
    capability: CapabilitySpec,
    failures: list[FailedTrace],
    embedder: Embedder | None = None,
    llm: LLMClient | None = None,
    min_cluster_size: int = 3,
    summarize: bool = True,
) -> ClusteringResult:
    """Embed + cluster failures; optionally label each cluster with an LLM."""
    if not failures:
        return ClusteringResult(capability_id=capability.id, clusters=[], noise_trace_ids=[])

    emb = embedder or auto_embedder()
    texts = [f.signature() for f in failures]
    matrix = await emb.embed(texts)
    matrix = np.nan_to_num(np.asarray(matrix, dtype=np.float32))

    # HDBSCAN expects at least ``min_cluster_size`` points. If we don't have
    # that many failures yet, treat them all as a single provisional cluster.
    if len(failures) < max(min_cluster_size, 2):
        cluster = Cluster(
            id=f"{capability.id}-c0",
            capability_id=capability.id,
            label="insufficient data",
            size=len(failures),
            trace_ids=[f.trace_id for f in failures],
        )
        return ClusteringResult(
            capability_id=capability.id,
            clusters=[cluster],
            noise_trace_ids=[],
        )

    clusterer = HDBSCAN(min_cluster_size=min_cluster_size, metric="euclidean")
    labels = clusterer.fit_predict(matrix)

    buckets: dict[int, list[int]] = {}
    noise: list[int] = []
    for idx, lab in enumerate(labels):
        if lab == -1:
            noise.append(idx)
        else:
            buckets.setdefault(int(lab), []).append(idx)

    clusters: list[Cluster] = []
    labeller = llm if llm is not None else (auto_client() if summarize else None)

    if not buckets and len(failures) >= min_cluster_size:
        summary = "needs review"
        if labeller is not None and summarize:
            summary = await _summarize(labeller, capability, failures)
        return ClusteringResult(
            capability_id=capability.id,
            clusters=[
                Cluster(
                    id=f"{capability.id}-c0",
                    capability_id=capability.id,
                    label=summary,
                    size=len(failures),
                    trace_ids=[f.trace_id for f in failures],
                )
            ],
            noise_trace_ids=[],
        )

    for lab, idxs in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        trace_ids = [failures[i].trace_id for i in idxs]
        summary = "cluster"
        if labeller is not None and summarize:
            summary = await _summarize(labeller, capability, [failures[i] for i in idxs])
        clusters.append(
            Cluster(
                id=f"{capability.id}-c{lab}",
                capability_id=capability.id,
                label=summary,
                size=len(idxs),
                trace_ids=trace_ids,
            )
        )

    return ClusteringResult(
        capability_id=capability.id,
        clusters=clusters,
        noise_trace_ids=[failures[i].trace_id for i in noise],
    )


_SUMMARIZE_SYSTEM = """\
You label clusters of failed LLM outputs. Given a capability and 3-8
representative (prompt, output) pairs, return a short (<=14 word) label
that captures the common failure mode.

Reply with strict JSON only:

  { "label": "<short label>" }
"""


async def _summarize(
    llm: LLMClient, capability: CapabilitySpec, examples: list[FailedTrace]
) -> str:
    sample = examples[:8]
    body_lines = [f"Capability: {capability.name} ({capability.id})"]
    body_lines.append(f"Description: {capability.description}")
    body_lines.append("Examples:")
    for i, tr in enumerate(sample, 1):
        body_lines.append(f"--- Example {i} ---")
        body_lines.append(f"Prompt: {tr.input[:500]}")
        body_lines.append(f"Output: {tr.output[:500]}")
    try:
        raw = await llm.complete(
            system=_SUMMARIZE_SYSTEM, user="\n".join(body_lines), json_mode=True
        )
        from flychain_capability_compiler.llm import parse_json_strict

        data = parse_json_strict(raw)
        label = str(data.get("label") or "").strip()
        return label or "unlabeled cluster"
    except Exception as exc:  # pragma: no cover - depends on LLM backend
        logger.warning("cluster summarize failed: %s", exc)
        return "unlabeled cluster"


# ---------------------------------------------------------------------------
# Dataset synthesis
# ---------------------------------------------------------------------------


async def synthesize_sft_dataset(
    *,
    capability: CapabilitySpec,
    cluster: Cluster,
    failures: Iterable[FailedTrace],
    llm: LLMClient | None = None,
    generate_missing: bool = True,
) -> list[dict[str, Any]]:
    """Return ``(prompt, ideal_response)`` rows for SFT.

    For each failed trace in the cluster:
      - if the trace carries a ``corrected_response`` (from ``/v1/feedback``),
        use it verbatim as the gold response.
      - otherwise, when ``generate_missing`` is True, ask the local judge /
        stronger model to produce an ideal response per the capability.
    """
    by_id = {f.trace_id: f for f in failures}
    selected = [by_id[t] for t in cluster.trace_ids if t in by_id]
    if not selected:
        return []

    labeller = llm if llm is not None else (auto_client() if generate_missing else None)
    rows: list[dict[str, Any]] = []
    for tr in selected:
        ideal = tr.corrected_response
        if ideal is None and generate_missing and labeller is not None:
            ideal = await _generate_ideal_response(labeller, capability, tr)
        if not ideal:
            continue
        rows.append(
            {
                "trace_id": tr.trace_id,
                "messages": [
                    {"role": "user", "content": tr.input},
                    {"role": "assistant", "content": ideal},
                ],
                "prompt": tr.input,
                "completion": ideal,
                "capability_id": capability.id,
                "cluster_id": cluster.id,
            }
        )
    return rows


async def synthesize_dpo_dataset(
    *,
    capability: CapabilitySpec,
    cluster: Cluster,
    failures: Iterable[FailedTrace],
    llm: LLMClient | None = None,
    generate_missing: bool = True,
) -> list[dict[str, Any]]:
    """Return ``(prompt, chosen, rejected)`` rows for DPO/KTO.

    Chosen comes from ``corrected_response`` when available, otherwise from
    an LLM-generated ideal. Rejected is the original failing output.
    """
    by_id = {f.trace_id: f for f in failures}
    selected = [by_id[t] for t in cluster.trace_ids if t in by_id]
    if not selected:
        return []

    labeller = llm if llm is not None else (auto_client() if generate_missing else None)
    rows: list[dict[str, Any]] = []
    for tr in selected:
        chosen = tr.corrected_response
        if chosen is None and generate_missing and labeller is not None:
            chosen = await _generate_ideal_response(labeller, capability, tr)
        if not chosen or chosen == tr.output:
            continue
        rows.append(
            {
                "trace_id": tr.trace_id,
                "prompt": tr.input,
                "chosen": chosen,
                "rejected": tr.output,
                "capability_id": capability.id,
                "cluster_id": cluster.id,
            }
        )
    return rows


_IDEAL_SYSTEM = """\
You are an expert model trainer. Given a capability and a failed (prompt,
output) pair, produce an **ideal** response that would have passed the
capability's eval. Be terse. Do not reproduce the failing output. Only
output the ideal response text - no prose, no JSON, no markdown code
fences.
"""


async def _generate_ideal_response(
    llm: LLMClient, capability: CapabilitySpec, trace: FailedTrace
) -> str:
    user = (
        f"Capability: {capability.name} ({capability.id})\n"
        f"Description: {capability.description}\n\n"
        f"Prompt:\n{trace.input}\n\n"
        f"Failing output:\n{trace.output}\n\n"
        "Produce the ideal response text."
    )
    try:
        return (await llm.complete(system=_IDEAL_SYSTEM, user=user)).strip()
    except Exception as exc:  # pragma: no cover
        logger.warning("ideal response generation failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
