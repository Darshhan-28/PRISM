"""Common tool models — Pydantic, offline, deterministic."""

from typing import Any, Literal
from pydantic import BaseModel, Field


class EvidenceRef(BaseModel):
    document_id: str | None = None
    chunk_id: str | None = None
    filename: str | None = None
    page_number: int | None = None
    line_range: tuple[int, int] | None = None
    sha256: str | None = None
    source_path: str | None = None
    score: float | None = None


class ToolOutput(BaseModel):
    success: bool
    result: Any = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    error: str | None = None
    truncated: bool = False
    execution_ms: int = 0
