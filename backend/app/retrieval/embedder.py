"""EmbeddingAdapter protocol + MockEmbedder (deterministic, offline)."""

import hashlib
import math
from typing import Protocol

from backend.app.config import get_config


class EmbeddingAdapter(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    @property
    def dim(self) -> int:
        ...


class MockEmbedder:
    """Deterministic hash-based embedder for tests — no model, no network."""

    def __init__(self, dim: int | None = None):
        cfg = get_config()
        self._dim = dim or cfg.embedding_dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            # hash text to seed deterministic vector
            h = hashlib.sha256(t.encode("utf-8")).digest()
            # expand to dim by cycling hash bytes
            vec = []
            for i in range(self._dim):
                b = h[i % len(h)]
                # map byte 0-255 to -1..1
                v = (b / 127.5) - 1.0
                vec.append(v)
            # L2 normalize
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vec = [x / norm for x in vec]
            out.append(vec)
        return out


class FastEmbedEmbedder:
    """Wrapper around fastembed — lazy import, only used when provider=fastembed."""

    def __init__(self, model_name: str | None = None, dim: int | None = None):
        cfg = get_config()
        self.model_name = model_name or cfg.embedding_model
        self._dim = dim or cfg.embedding_dim
        self._model = None

    @property
    def dim(self) -> int:
        return self._dim

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        embeddings = list(model.embed(texts))
        # fastembed returns numpy arrays; convert to list
        return [e.tolist() if hasattr(e, "tolist") else list(e) for e in embeddings]


def get_embedder() -> EmbeddingAdapter:
    cfg = get_config()
    provider = cfg.embedding_provider.lower()
    if provider == "mock":
        return MockEmbedder()
    elif provider == "fastembed":
        return FastEmbedEmbedder()
    elif provider == "sentence_transformers":
        # placeholder — would require torch
        raise ValueError("sentence_transformers provider not implemented in Phase 2; use mock or fastembed")
    else:
        raise ValueError(f"Unknown embedding_provider: {provider}")
