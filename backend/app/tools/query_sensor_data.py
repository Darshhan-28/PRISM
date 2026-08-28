"""query_sensor_data — deterministic SQLite query, no LLM, no network."""

import time
import re
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, field_validator

from backend.app.tools.common import ToolOutput, EvidenceRef
from backend.app.store.db import get_connection


class QuerySensorDataInput(BaseModel):
    equipment_id: str = Field(..., min_length=1, max_length=50, description="Equipment ID e.g. P-204")
    metric: str | None = Field(default=None, max_length=50)
    start_time: datetime | None = None
    end_time: datetime | None = None
    aggregation: Literal["raw", "avg", "max", "min"] = "raw"

    @field_validator("equipment_id")
    @classmethod
    def validate_equipment(cls, v):
        if not re.match(r"^[A-Za-z0-9_\-]+$", v):
            raise ValueError("equipment_id must be alphanumeric with _-")
        return v

    @field_validator("metric")
    @classmethod
    def validate_metric(cls, v):
        if v is None:
            return v
        if not re.match(r"^[A-Za-z0-9_\-]+$", v):
            raise ValueError("metric must be alphanumeric with _-")
        return v


def query_sensor_data(inp: QuerySensorDataInput, db_path=None) -> ToolOutput:
    start = time.time()
    try:
        # Time range sanity
        if inp.start_time and inp.end_time and inp.start_time > inp.end_time:
            return ToolOutput(success=False, result=None, evidence_refs=[], error="start_time must be <= end_time", execution_ms=int((time.time() - start) * 1000))

        conn = get_connection(db_path)
        try:
            # Parameterized query only — no string interpolation
            query = "SELECT id, equipment_id, timestamp, metric, value, source_file, doc_id FROM sensor_events WHERE equipment_id = ?"
            params: list = [inp.equipment_id]
            if inp.metric:
                query += " AND metric = ?"
                params.append(inp.metric)
            if inp.start_time:
                query += " AND timestamp >= ?"
                params.append(inp.start_time.isoformat())
            if inp.end_time:
                query += " AND timestamp <= ?"
                params.append(inp.end_time.isoformat())
            query += " ORDER BY timestamp"

            cur = conn.execute(query, params)
            rows = cur.fetchall()

            if not rows:
                return ToolOutput(success=True, result=[], evidence_refs=[], error=None, execution_ms=int((time.time() - start) * 1000))

            # Aggregation
            if inp.aggregation == "raw":
                result = [
                    {
                        "id": r["id"],
                        "equipment_id": r["equipment_id"],
                        "timestamp": r["timestamp"],
                        "metric": r["metric"],
                        "value": r["value"],
                        "source_file": r["source_file"],
                        "doc_id": r["doc_id"],
                    }
                    for r in rows
                ]
            else:
                values = [r["value"] for r in rows]
                if inp.aggregation == "avg":
                    agg_val = sum(values) / len(values) if values else None
                elif inp.aggregation == "max":
                    agg_val = max(values) if values else None
                else:
                    agg_val = min(values) if values else None
                result = {"aggregation": inp.aggregation, "value": agg_val, "count": len(rows), "equipment_id": inp.equipment_id, "metric": inp.metric}

            # Evidence refs: one per row (or aggregated single ref)
            if inp.aggregation == "raw":
                evidence_refs = [
                    EvidenceRef(
                        document_id=r["doc_id"],
                        chunk_id=None,
                        filename=r["source_file"].split("/")[-1].split("\\")[-1] if r["source_file"] else None,
                        source_path=r["source_file"],
                    )
                    for r in rows
                ]
            else:
                # aggregated -> reference first row's source
                evidence_refs = [
                    EvidenceRef(
                        document_id=rows[0]["doc_id"],
                        filename=rows[0]["source_file"].split("/")[-1].split("\\")[-1] if rows[0]["source_file"] else None,
                        source_path=rows[0]["source_file"],
                    )
                ]

            return ToolOutput(success=True, result=result, evidence_refs=evidence_refs, error=None, execution_ms=int((time.time() - start) * 1000))
        finally:
            conn.close()
    except Exception as e:
        return ToolOutput(success=False, result=None, evidence_refs=[], error=f"{type(e).__name__}: {e}", execution_ms=int((time.time() - start) * 1000))
