"""Append-only audit logger — SQLite workbench.db, no secrets."""

import json
import hashlib
import time
from datetime import datetime, timezone
from typing import Any
from pathlib import Path
from pydantic import BaseModel, Field

from backend.app.store.db import get_connection

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    tool TEXT,
    sanitized_input TEXT,
    success INTEGER,
    execution_ms INTEGER,
    evidence_refs TEXT,
    error_code TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_investigation ON audit_log(investigation_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
"""

def _ensure_audit_table(db_path=None):
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()

def _sanitize(value: Any, max_len: int = 1000) -> str:
    try:
        s = json.dumps(value, default=str, ensure_ascii=False)
    except Exception:
        s = str(value)
    # Remove secrets patterns: rudimentary
    # Truncate
    if len(s) > max_len:
        s = s[:max_len] + "...[truncated]"
    s = s.replace("\x00", "")
    return s

def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]

class AuditRecord(BaseModel):
    id: str
    investigation_id: str
    timestamp: str
    event_type: str
    tool: str | None = None
    sanitized_input: str | None = None
    success: bool | None = None
    execution_ms: int | None = None
    evidence_refs: str | None = None  # JSON string
    error_code: str | None = None

def log_event(
    investigation_id: str,
    event_type: str,
    tool: str | None = None,
    raw_input: Any | None = None,
    success: bool | None = None,
    execution_ms: int | None = None,
    evidence_refs: Any | None = None,
    error_code: str | None = None,
    db_path=None,
) -> str:
    _ensure_audit_table(db_path)
    ts = datetime.now(timezone.utc).isoformat()
    # Sanitized input: never store raw document contents, truncate
    sanitized = _sanitize(raw_input) if raw_input is not None else None
    # Evidence refs: store as sanitized JSON (provenance only)
    ev_str = _sanitize(evidence_refs, max_len=2000) if evidence_refs is not None else None
    # Deterministic id: hash of investigation_id + event_type + timestamp + tool
    raw_id = f"{investigation_id}:{event_type}:{tool or ''}:{ts}:{_hash(sanitized or '')}"
    aid = hashlib.sha256(raw_id.encode()).hexdigest()[:16]
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO audit_log (id, investigation_id, timestamp, event_type, tool, sanitized_input, success, execution_ms, evidence_refs, error_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (aid, investigation_id, ts, event_type, tool, sanitized, 1 if success else (0 if success is False else None), execution_ms, ev_str, error_code),
        )
        conn.commit()
    finally:
        conn.close()
    return aid

def get_audit_log(investigation_id: str, db_path=None) -> list[dict[str, Any]]:
    _ensure_audit_table(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.execute("SELECT * FROM audit_log WHERE investigation_id=? ORDER BY timestamp", (investigation_id,))
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def count_audit_logs(investigation_id: str | None = None, db_path=None) -> int:
    _ensure_audit_table(db_path)
    conn = get_connection(db_path)
    try:
        if investigation_id:
            cur = conn.execute("SELECT COUNT(*) FROM audit_log WHERE investigation_id=?", (investigation_id,))
        else:
            cur = conn.execute("SELECT COUNT(*) FROM audit_log")
        return cur.fetchone()[0]
    finally:
        conn.close()
