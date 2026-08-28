"""search_maintenance_logs — deterministic SQLite query, no LLM."""

import time
import re
from datetime import date
from pydantic import BaseModel, Field, field_validator

from backend.app.tools.common import ToolOutput, EvidenceRef
from backend.app.store.db import get_connection


class SearchMaintenanceLogsInput(BaseModel):
    equipment_id: str = Field(..., min_length=1, max_length=50)
    start_date: date | None = None
    end_date: date | None = None
    keyword: str | None = Field(default=None, max_length=100)

    @field_validator("equipment_id")
    @classmethod
    def validate_equipment(cls, v):
        if not re.match(r"^[A-Za-z0-9_\-]+$", v):
            raise ValueError("equipment_id must be alphanumeric with _-")
        return v


def search_maintenance_logs(inp: SearchMaintenanceLogsInput, db_path=None) -> ToolOutput:
    start = time.time()
    try:
        if inp.start_date and inp.end_date and inp.start_date > inp.end_date:
            return ToolOutput(success=False, result=None, evidence_refs=[], error="start_date must be <= end_date", execution_ms=int((time.time() - start) * 1000))
        if inp.keyword and len(inp.keyword.strip()) == 0:
            return ToolOutput(success=False, result=None, evidence_refs=[], error="keyword must be non-empty if provided", execution_ms=int((time.time() - start) * 1000))

        conn = get_connection(db_path)
        try:
            query = "SELECT id, equipment_id, date, action, technician, source_file, doc_id FROM maintenance_logs WHERE equipment_id = ?"
            params: list = [inp.equipment_id]
            if inp.start_date:
                query += " AND date >= ?"
                params.append(inp.start_date.isoformat())
            if inp.end_date:
                query += " AND date <= ?"
                params.append(inp.end_date.isoformat())
            if inp.keyword:
                query += " AND action LIKE ?"
                params.append(f"%{inp.keyword}%")
            query += " ORDER BY date"

            cur = conn.execute(query, params)
            rows = cur.fetchall()

            result = [
                {
                    "id": r["id"],
                    "equipment_id": r["equipment_id"],
                    "date": r["date"],
                    "action": r["action"],
                    "technician": r["technician"],
                    "source_file": r["source_file"],
                    "doc_id": r["doc_id"],
                }
                for r in rows
            ]

            evidence_refs = [
                EvidenceRef(
                    document_id=r["doc_id"],
                    filename=r["source_file"].split("/")[-1].split("\\")[-1] if r["source_file"] else None,
                    source_path=r["source_file"],
                )
                for r in rows
            ]

            return ToolOutput(success=True, result=result, evidence_refs=evidence_refs, error=None, execution_ms=int((time.time() - start) * 1000))
        finally:
            conn.close()
    except Exception as e:
        return ToolOutput(success=False, result=None, evidence_refs=[], error=f"{type(e).__name__}: {e}", execution_ms=int((time.time() - start) * 1000))
