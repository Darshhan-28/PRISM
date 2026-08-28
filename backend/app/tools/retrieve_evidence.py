"""retrieve_evidence — exact fetch by chunk_id or document_id from SQLite."""

import time
import re
from pydantic import BaseModel, Field, model_validator

from backend.app.tools.common import ToolOutput, EvidenceRef
from backend.app.store.db import get_connection


class RetrieveEvidenceInput(BaseModel):
    chunk_id: str | None = Field(default=None, description="Exact chunk ID")
    document_id: str | None = Field(default=None, description="Document ID to fetch all chunks")

    @model_validator(mode="after")
    def check_at_least_one(self):
        if not self.chunk_id and not self.document_id:
            raise ValueError("Either chunk_id or document_id must be provided")
        if self.chunk_id and self.document_id:
            raise ValueError("Provide only one of chunk_id or document_id, not both")
        # Basic ID format: alphanumeric, :, -, _
        pattern = re.compile(r"^[A-Za-z0-9:_\-]+$")
        if self.chunk_id and not pattern.match(self.chunk_id):
            raise ValueError("Invalid chunk_id format")
        if self.document_id and not pattern.match(self.document_id):
            raise ValueError("Invalid document_id format")
        return self


def retrieve_evidence(inp: RetrieveEvidenceInput, db_path=None) -> ToolOutput:
    start = time.time()
    try:
        conn = get_connection(db_path)
        try:
            if inp.chunk_id:
                cur = conn.execute(
                    "SELECT c.id as chunk_id, c.doc_id as document_id, c.text, c.page_number, c.line_start, c.line_end, c.section, c.token_estimate, c.char_count, d.filename, d.source_path, d.sha256, d.file_type FROM chunks c JOIN documents d ON c.doc_id = d.id WHERE c.id = ?",
                    (inp.chunk_id,),
                )
                row = cur.fetchone()
                if not row:
                    return ToolOutput(
                        success=False,
                        result=None,
                        evidence_refs=[],
                        error=f"chunk_id not found: {inp.chunk_id}",
                        execution_ms=int((time.time() - start) * 1000),
                    )
                result = {
                    "chunk_id": row["chunk_id"],
                    "document_id": row["document_id"],
                    "text": row["text"],
                    "filename": row["filename"],
                    "file_type": row["file_type"],
                    "source_path": row["source_path"],
                    "sha256": row["sha256"],
                    "page_number": row["page_number"],
                    "line_range": (row["line_start"], row["line_end"]) if row["line_start"] is not None else None,
                    "section": row["section"],
                }
                evidence_ref = EvidenceRef(
                    chunk_id=row["chunk_id"],
                    document_id=row["document_id"],
                    filename=row["filename"],
                    page_number=row["page_number"],
                    line_range=(row["line_start"], row["line_end"]) if row["line_start"] is not None else None,
                    sha256=row["sha256"],
                    source_path=row["source_path"],
                )
                return ToolOutput(
                    success=True,
                    result=result,
                    evidence_refs=[evidence_ref],
                    error=None,
                    execution_ms=int((time.time() - start) * 1000),
                )
            else:
                # document_id -> fetch document + all chunks
                cur = conn.execute("SELECT * FROM documents WHERE id = ?", (inp.document_id,))
                doc = cur.fetchone()
                if not doc:
                    return ToolOutput(
                        success=False,
                        result=None,
                        evidence_refs=[],
                        error=f"document_id not found: {inp.document_id}",
                        execution_ms=int((time.time() - start) * 1000),
                    )
                cur = conn.execute("SELECT * FROM chunks WHERE doc_id = ? ORDER BY id", (inp.document_id,))
                rows = cur.fetchall()
                result = {
                    "document": {
                        "id": doc["id"],
                        "filename": doc["filename"],
                        "file_type": doc["file_type"],
                        "source_path": doc["source_path"],
                        "sha256": doc["sha256"],
                        "title": doc["title"],
                        "ingested_at": doc["ingested_at"],
                        "page_count": doc["page_count"],
                    },
                    "chunks": [
                        {
                            "chunk_id": r["id"],
                            "text": r["text"],
                            "page_number": r["page_number"],
                            "line_range": (r["line_start"], r["line_end"]) if r["line_start"] is not None else None,
                            "section": r["section"],
                        }
                        for r in rows
                    ],
                }
                evidence_refs = [
                    EvidenceRef(
                        chunk_id=r["id"],
                        document_id=doc["id"],
                        filename=doc["filename"],
                        page_number=r["page_number"],
                        line_range=(r["line_start"], r["line_end"]) if r["line_start"] is not None else None,
                        sha256=doc["sha256"],
                        source_path=doc["source_path"],
                    )
                    for r in rows
                ]
                return ToolOutput(
                    success=True,
                    result=result,
                    evidence_refs=evidence_refs,
                    error=None,
                    execution_ms=int((time.time() - start) * 1000),
                )
        finally:
            conn.close()
    except Exception as e:
        return ToolOutput(
            success=False,
            result=None,
            evidence_refs=[],
            error=f"{type(e).__name__}: {e}",
            execution_ms=int((time.time() - start) * 1000),
        )
