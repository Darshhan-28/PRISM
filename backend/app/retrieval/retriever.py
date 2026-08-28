"""Retriever — query → embed → vector search → threshold + metadata filter.

CPU-only, deterministic, offline. Keeps full provenance for evidence.
"""

from typing import Any
from pydantic import BaseModel

from backend.app.config import get_config
from backend.app.retrieval.embedder import get_embedder
from backend.app.retrieval.vector_store import get_vector_store


class RetrievedChunk(BaseModel):
    chunk_id: str
    text: str
    score: float  # cosine similarity 0..1 (higher is better)
    distance: float | None = None  # raw chroma distance
    metadata: dict[str, Any]


ALLOWED_FILTER_KEYS = {"document_id", "filename", "file_type", "sha256", "title", "section", "page_number", "chunk_id"}


class Retriever:
    def __init__(self, embedder=None, vector_store=None):
        self.embedder = embedder or get_embedder()
        self.vector_store = vector_store or get_vector_store()
        self.cfg = get_config()

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        threshold: float | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievedChunk]:
        if not query or not query.strip():
            raise ValueError("query must be non-empty")
        if len(query) > 2000:
            raise ValueError("query too long (max 2000 chars)")
        k = top_k if top_k is not None else self.cfg.retrieval_top_k
        if not 1 <= k <= 20:
            raise ValueError("top_k must be 1..20")
        thr = threshold if threshold is not None else self.cfg.retrieval_threshold

        if filters:
            invalid = set(filters.keys()) - ALLOWED_FILTER_KEYS
            if invalid:
                raise ValueError(f"Invalid filter keys: {invalid}. Allowed: {ALLOWED_FILTER_KEYS}")

        # Embed query
        q_emb = self.embedder.embed([query])[0]

        # Search — Chroma where filter: exact match, but it expects proper types
        # Convert page_number to int if needed
        where = None
        if filters:
            where = {}
            for key, val in filters.items():
                # Chroma stores page_number as int, others as str
                if key == "page_number":
                    where[key] = int(val)
                else:
                    where[key] = str(val)

        raw_hits = self.vector_store.search(q_emb, top_k=k, filters=where)

        # Convert distance → similarity and apply threshold
        out: list[RetrievedChunk] = []
        for h in raw_hits:
            dist = h.get("distance")
            if dist is None:
                score = 0.0
            else:
                try:
                    score = 1.0 - float(dist)
                except Exception:
                    score = 0.0
            # Apply threshold only if thr > 0 (thr=0 means no filtering)
            if thr > 0 and score < thr:
                continue
            meta = h.get("metadata") or {}
            out.append(
                RetrievedChunk(
                    chunk_id=h.get("chunk_id") or meta.get("chunk_id", ""),
                    text=h.get("text") or "",
                    score=score,
                    distance=dist,
                    metadata=meta,
                )
            )
        return out

    def retrieve_with_scores(self, *args, **kwargs) -> list[RetrievedChunk]:
        """Alias for retrieve — kept for backwards compat."""
        return self.retrieve(*args, **kwargs)
