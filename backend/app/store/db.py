"""SQLite store — documents + chunks tables at data/workbench.db."""

import sqlite3
from pathlib import Path
from datetime import datetime

from backend.app.config import get_config

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    source_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    title TEXT,
    ingested_at TEXT NOT NULL,
    page_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number INTEGER,
    text TEXT NOT NULL,
    token_estimate INTEGER NOT NULL,
    char_count INTEGER NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    section TEXT,
    ingested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);
CREATE TABLE IF NOT EXISTS sensor_events (
    id TEXT PRIMARY KEY,
    equipment_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    source_file TEXT NOT NULL,
    doc_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_sensor_equipment ON sensor_events(equipment_id);
CREATE INDEX IF NOT EXISTS idx_sensor_timestamp ON sensor_events(timestamp);
CREATE TABLE IF NOT EXISTS maintenance_logs (
    id TEXT PRIMARY KEY,
    equipment_id TEXT NOT NULL,
    date TEXT NOT NULL,
    action TEXT NOT NULL,
    technician TEXT,
    source_file TEXT NOT NULL,
    doc_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_maint_equipment ON maintenance_logs(equipment_id);
"""


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or get_config().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: Path | None = None) -> None:
    path = db_path or get_config().db_path
    conn = get_connection(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def insert_document(conn: sqlite3.Connection, doc) -> None:
    """Insert NormalizedDocument. Caller manages transaction."""
    conn.execute(
        "INSERT OR REPLACE INTO documents (id, filename, file_type, source_path, sha256, title, ingested_at, page_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            doc.document_id,
            doc.filename,
            doc.file_type,
            doc.source_path,
            doc.sha256,
            doc.title,
            doc.ingested_at.isoformat(),
            len(doc.pages),
        ),
    )


def insert_chunks(conn: sqlite3.Connection, chunks) -> None:
    for c in chunks:
        meta = c.metadata
        conn.execute(
            "INSERT OR REPLACE INTO chunks (id, doc_id, page_number, text, token_estimate, char_count, line_start, line_end, section, ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                c.chunk_id,
                c.document_id,
                meta.page_number,
                c.text,
                meta.token_estimate,
                meta.char_count,
                meta.line_range[0] if meta.line_range else None,
                meta.line_range[1] if meta.line_range else None,
                meta.section,
                meta.ingested_at.isoformat(),
            ),
        )


def count_documents(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM documents")
    return cur.fetchone()[0]


def count_chunks(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM chunks")
    return cur.fetchone()[0]


def delete_document(conn: sqlite3.Connection, doc_id: str) -> tuple[int, int]:
    cur1 = conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    chunks_deleted = cur1.rowcount
    cur2 = conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    docs_deleted = cur2.rowcount
    return docs_deleted, chunks_deleted


def fetch_chunks_by_doc(conn: sqlite3.Connection, doc_id: str) -> list[sqlite3.Row]:
    cur = conn.execute("SELECT * FROM chunks WHERE doc_id=? ORDER BY id", (doc_id,))
    return cur.fetchall()


def insert_sensor_event(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sensor_events (id, equipment_id, timestamp, metric, value, source_file, doc_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (row["id"], row["equipment_id"], row["timestamp"], row["metric"], float(row["value"]), row["source_file"], row.get("doc_id")),
    )


def insert_maintenance_log(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO maintenance_logs (id, equipment_id, date, action, technician, source_file, doc_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (row["id"], row["equipment_id"], row["date"], row["action"], row.get("technician"), row["source_file"], row.get("doc_id")),
    )
