"""search_documents — deterministic local vector search via Retriever."""

import time
from typing import Any
from pydantic import BaseModel, Field, field_validator

from backend.app.tools.common import ToolOutput, EvidenceRef
from backend.app.retrieval.retriever import Retriever, ALLOWED_FILTER_KEYS


class SearchDocumentsInput(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="Search query")
    top_k: int = Field(default=6, ge=1, le=20)
    filters: dict[str, Any] | None = None

    @field_validator("filters")
    @classmethod
    def validate_filters(cls, v):
        if v is None:
            return v
        if not isinstance(v, dict):
            raise ValueError("filters must be dict or None")
        invalid = set(v.keys()) - ALLOWED_FILTER_KEYS
        if invalid:
            raise ValueError(f"Invalid filter keys: {invalid}. Allowed: {ALLOWED_FILTER_KEYS}")
        return v


def search_documents(inp: SearchDocumentsInput, retriever: Retriever | None = None) -> ToolOutput:
    start = time.time()
    try:
        # No file access, no network, no LLM — pure retriever call
        retr = retriever or Retriever()
        hits = retr.retrieve(query=inp.query, top_k=inp.top_k, filters=inp.filters)
        result = []
        evidence_refs = []
        for h in hits:
            meta = h.metadata or {}
            result.append(
                {
                    "chunk_id": h.chunk_id,
                    "text": h.text,
                    "score": h.score,
                    "distance": h.distance,
                    "filename": meta.get("filename"),
                    "document_id": meta.get("document_id"),
                    "page_number": meta.get("page_number") if meta.get("page_number") != -1 else None,
                    "sha256": meta.get("sha256"),
                    "source_path": meta.get("source_path"),
                }
            )
            evidence_refs.append(
                EvidenceRef(
                    chunk_id=h.chunk_id,
                    document_id=meta.get("document_id"),
                    filename=meta.get("filename"),
                    page_number=meta.get("page_number") if meta.get("page_number") != -1 else None,
                    sha256=meta.get("sha256"),
                    source_path=meta.get("source_path"),
                    score=h.score,
                )
            )
        return ToolOutput(
            success=True,
            result=result,
            evidence_refs=evidence_refs,
            error=None,
            execution_ms=int((time.time() - start) * 1000),
        )
    except Exception as e:
        return ToolOutput(
            success=False,
            result=None,
            evidence_refs=[],
            error=f"{type(e).__name__}: {e}",
            execution_ms=int((time.time() - start) * 1000),
        )
