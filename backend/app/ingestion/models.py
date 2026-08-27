"""Normalized document and chunk models — evidence provenance."""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

FileType = Literal["pdf", "csv", "json", "txt", "log", "md"]


class Page(BaseModel):
    page_number: int = Field(ge=1)
    text: str
    char_count: int = Field(ge=0)
    section: str | None = None
    is_scanned: bool = False


class NormalizedDocument(BaseModel):
    document_id: str
    filename: str
    file_type: FileType
    source_path: str
    sha256: str
    title: str | None = None
    ingested_at: datetime
    pages: list[Page]
    structured_rows: list[dict] | None = None
    warnings: list[str] = Field(default_factory=list)


class ChunkMetadata(BaseModel):
    document_id: str
    filename: str
    file_type: str
    source_path: str
    sha256: str
    chunk_id: str
    page_number: int | None = None
    line_range: tuple[int, int] | None = None
    section: str | None = None
    title: str | None = None
    ingested_at: datetime
    chunk_index: int = Field(ge=0)
    token_estimate: int = Field(ge=0)
    char_count: int = Field(ge=0)


class Chunk(BaseModel):
    chunk_id: str
    document_id: str
    text: str
    metadata: ChunkMetadata


# Ingestion result types

class IngestResult(BaseModel):
    source_path: str
    filename: str
    status: Literal["ingested", "rejected", "failed"]
    document_id: str | None = None
    file_type: str | None = None
    sha256: str | None = None
    pages: int | None = None
    chunks: int | None = None
    error_code: str | None = None
    message: str | None = None


class IngestionReport(BaseModel):
    total_files: int
    ingested: int
    rejected: int
    failed: int
    total_chunks: int
    total_pages: int
    duration_ms: int
    per_file: list[IngestResult]
