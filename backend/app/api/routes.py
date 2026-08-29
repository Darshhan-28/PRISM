"""API routes — minimal, reuse existing modules, no business logic duplication."""

import json
import time
import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, UploadFile, File, HTTPException, Query
from pydantic import BaseModel

from backend.app.config import get_config
from backend.app.store.db import get_connection, init_db
from backend.app.ingestion.pipeline import IngestionPipeline
from backend.app.orchestrator.investigation import InvestigationOrchestrator
from backend.app.retrieval.vector_store import ChromaStore
from backend.app.evidence.graph import EvidenceGraph
from backend.app.audit.logger import get_audit_log, count_audit_logs
from backend.app.evidence.contradictions import find_contradictions

router = APIRouter()

class InvestigateRequest(BaseModel):
    objective: str

class QueryRequest(BaseModel):
    query: str

@router.get("/health")
def health():
    cfg = get_config()
    init_db()
    # counts
    try:
        conn = get_connection()
        docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        inv = conn.execute("SELECT COUNT(*) FROM investigations").fetchone()[0] if _has_table(conn, "investigations") else 0
        conn.close()
    except Exception:
        docs = chunks = inv = 0
    try:
        vs = ChromaStore()
        vs_count = vs.count()
    except Exception:
        vs_count = 0
    return {"status": "ok", "documents": docs, "chunks": chunks, "vector_store": vs_count, "investigations": inv, "offline": True, "mock_default": True}

def _has_table(conn, name: str) -> bool:
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
        return cur.fetchone() is not None
    except Exception:
        return False

@router.get("/status")
def status():
    return health()

@router.get("/documents")
def list_documents():
    init_db()
    conn = get_connection()
    try:
        rows = conn.execute("SELECT id, filename, file_type, sha256, page_count, ingested_at FROM documents ORDER BY ingested_at DESC").fetchall()
        return {"documents": [dict(r) for r in rows]}
    finally:
        conn.close()

@router.post("/ingest")
async def ingest(file: UploadFile = File(...)):
    cfg = get_config()
    data = await file.read()
    if len(data) > cfg.max_file_size_bytes:
        raise HTTPException(status_code=413, detail="file too large")
    # Save to raw
    target = cfg.raw_dir / file.filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    # Run pipeline
    pipeline = IngestionPipeline()
    report = pipeline.ingest_files([target], embed_and_store=True)
    if report.ingested == 0:
        first = report.per_file[0] if report.per_file else None
        detail = "ingest failed"
        if first:
            parts = []
            if first.error_code:
                parts.append(first.error_code)
            if first.message:
                parts.append(first.message)
            detail += f": {' '.join(parts)} " if parts else ""
        raise HTTPException(status_code=400, detail=detail.strip())
    return {"ingested": report.ingested, "chunks": report.total_chunks, "filename": file.filename, "detail": report.per_file[0].model_dump() if report.per_file else {}}

@router.post("/investigations")
def create_investigation(req: InvestigateRequest):
    if not req.objective or len(req.objective.strip()) < 10:
        raise HTTPException(status_code=400, detail="objective too short")
    orch = InvestigationOrchestrator()
    report = orch.investigate(req.objective)
    return report.model_dump()

@router.get("/investigations/{inv_id}")
def get_investigation(inv_id: str):
    init_db()
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM investigations WHERE id=?", (inv_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="investigation not found")
        steps = conn.execute("SELECT * FROM investigation_steps WHERE investigation_id=? ORDER BY step_no", (inv_id,)).fetchall()
        return {"investigation": dict(row), "steps": [dict(s) for s in steps]}
    finally:
        conn.close()

@router.get("/investigations/{inv_id}/graph")
def get_graph(inv_id: str):
    # Build graph from investigation report stored in DB
    init_db()
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM investigations WHERE id=?", (inv_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        # Try load persisted graph
        graph = EvidenceGraph.load_from_db(inv_id)
        if graph.nodes:
            return graph.to_dict()
        # Fallback: build from investigation report via orchestrator DB
        # Reconstruct minimal report for graph
        steps_rows = conn.execute("SELECT * FROM investigation_steps WHERE investigation_id=? ORDER BY step_no", (inv_id,)).fetchall()
        from types import SimpleNamespace

        from backend.app.evidence.engine import EvidenceRef

        steps = []
        all_refs = []
        for r in steps_rows:
            refs = json.loads(r["evidence_refs"]) if r["evidence_refs"] else []
            ev_refs = [EvidenceRef(**x) for x in refs] if refs else []
            all_refs.extend(ev_refs)
            steps.append(
                SimpleNamespace(
                    step_no=r["step_no"],
                    tool=r["tool"],
                    input=json.loads(r["input"]),
                    rationale=r["rationale"],
                    success=bool(r["success"]),
                    evidence_refs=ev_refs,
                )
            )
        report_obj = SimpleNamespace(
            investigation_id=inv_id,
            objective=row["objective"],
            status=row["status"],
            evidence_state=None,
            summary=row["summary"] or "",
            steps_executed=steps,
            evidence_refs=all_refs,
        )
        graph = EvidenceGraph.from_investigation(report_obj)
        return graph.to_dict()
    finally:
        conn.close()

@router.get("/investigations/{inv_id}/audit")
def get_audit(inv_id: str):
    logs = get_audit_log(inv_id)
    return {"investigation_id": inv_id, "events": logs, "count": len(logs)}

@router.post("/query")
def query(req: QueryRequest):
    # Quick Q&A via answer_with_evidence (reuse evidence engine)
    from backend.app.retrieval.retriever import Retriever
    from backend.app.llm.factory import get_llm_adapter
    from backend.app.evidence.engine import answer_with_evidence
    retriever = Retriever()
    llm = get_llm_adapter()
    result = answer_with_evidence(req.query, retriever, llm)
    # Contradictions
    contr = find_contradictions([r for r in retriever.retrieve(req.query, top_k=6)])
    return {"answer": result.answer, "state": result.state, "evidence_refs": [r.model_dump() for r in result.evidence_refs], "contradictions": contr.model_dump() if hasattr(contr, "model_dump") else contr}

@router.get("/evidence/{chunk_id}")
def get_evidence(chunk_id: str):
    conn = get_connection()
    try:
        row = conn.execute("SELECT c.*, d.filename, d.sha256 as doc_sha FROM chunks c JOIN documents d ON c.doc_id=d.id WHERE c.id=?", (chunk_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="chunk not found")
        data = dict(row)
        # Wrap into a consistent shape: id, text, metadata
        metadata = {k: v for k, v in data.items() if k not in ("text", "id")}
        return {"id": data["id"], "text": data.get("text") or "", "metadata": metadata}
    finally:
        conn.close()
