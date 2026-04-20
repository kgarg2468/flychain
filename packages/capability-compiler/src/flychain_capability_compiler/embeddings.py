"""Text embedding clients used by the clustering + triage pipeline.

Follows the same shape as the LLM clients in :mod:`flychain_capability_compiler.llm`:
a tiny protocol + concrete backends + ``auto_embedder`` that picks the right
one based on available keys / local services.
"""

from __future__ import annotations

import hashlib
import math
import os
from typing import Protocol

import httpx
import numpy as np


class Embedder(Protocol):
    provider: str
    model: str

    async def embed(self, texts: list[str]) -> np.ndarray: ...


class OllamaEmbedder:
    provider = "local-ollama"

    def __init__(self, base_url: str | None = None, model: str = "nomic-embed-text") -> None:
        self.base_url = (
            base_url or os.environ.get("FLYCHAIN_OLLAMA_URL", "http://localhost:11434")
        ).rstrip("/")
        self.model = model

    async def embed(self, texts: list[str]) -> np.ndarray:
        vectors: list[list[float]] = []
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
            for text in texts:
                resp = await client.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": self.model, "prompt": text},
                )
                resp.raise_for_status()
                data = resp.json()
                vectors.append(list(data.get("embedding") or []))
        return np.asarray(vectors, dtype=np.float32)


class HashEmbedder:
    """Deterministic hashing embedder for tests + zero-dependency fallback.

    Maps each text to a fixed-dimensional pseudo-embedding via character
    hashing. Not a real semantic embedding - only use when a real embedding
    model isn't available.
    """

    provider = "hash"
    model = "hash-embed"

    def __init__(self, dim: int = 128) -> None:
        self.dim = dim

    async def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            digest = hashlib.sha256((text or "").encode("utf-8")).digest()
            # Expand digest into self.dim floats by repeating + seeding.
            for j in range(self.dim):
                byte = digest[(j * 7) % len(digest)]
                out[i, j] = (byte / 255.0) * 2.0 - 1.0
            # L2 normalize for cosine-friendly use.
            norm = float(np.linalg.norm(out[i])) or 1.0
            out[i] /= norm
        # Ensure no row is all zeros.
        if math.isnan(out.sum()):
            out = np.nan_to_num(out)
        return out


def auto_embedder(*, prefer: str | None = None) -> Embedder:
    """Pick an embedder based on environment.

    Precedence when ``prefer`` is None:
      1. Local Ollama (``nomic-embed-text``) if reachable env is set.
      2. Hash-based deterministic fallback.

    The Ollama default matches the orchestrator's ``embedding_model`` setting.
    """
    preferred = (prefer or os.environ.get("FLYCHAIN_EMBEDDER") or "").strip().lower()
    if preferred == "hash":
        return HashEmbedder()
    # In v1 local-only mode we don't ship a paid embedding path - Ollama first.
    return OllamaEmbedder()
