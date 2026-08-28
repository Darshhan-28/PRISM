"""
Offline API smoke tests using TestClient (no server needed).
All routes reuse existing Phase 2-11 modules; only mock adapters.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["offline"] is True
    assert data["mock_default"] is True
    assert isinstance(data["documents"], int) and data["documents"] >= 0


def test_documents_empty():
    r = client.get("/api/documents")
    assert r.status_code == 200
    data = r.json()
    # API returns {"documents": [ ... ]}
    if "documents" in data:
        docs = data["documents"]
    else:
        docs = data
    assert isinstance(docs, list)
    # Should have at least the 6 flagship samples
    assert len(docs) >= 6


def test_ingest_validation():
    # upload a non-allowed file type should reject (400/422)
    bad = b"<?xml version='1.0'?><rss><channel><title>bad</title></channel></rss>"
    files = {"file": ("evil.xml", bad, "application/xml")}
    r = client.post("/api/ingest", files=files)
    # Should be rejected by validator (400 or 422)
    assert r.status_code in (400, 413, 422)


def test_investigation_flow():
    # Minimal objective length >=10
    objective = "Investigate abnormal pressure event in Pump P-204 on 2026-08-15 for demo test case"
    r = client.post("/api/investigations", json={"objective": objective})
    assert r.status_code == 200
    inv = r.json()
    inv_id = inv["investigation_id"]
    assert inv["objective"] == objective
    assert inv["status"] in ("completed", "failed")
    # evidence_state must be one of the four
    assert inv["evidence_state"] in (
        "SUPPORTED",
        "PARTIALLY_SUPPORTED",
        "INSUFFICIENT_EVIDENCE",
        "CONFLICTING_EVIDENCE",
    )
    # summary non-empty
    assert isinstance(inv["summary"], str) and len(inv["summary"]) > 0
    # graph
    rg = client.get(f"/api/investigations/{inv_id}/graph")
    assert rg.status_code == 200
    graph = rg.json()
    assert "nodes" in graph and "edges" in graph
    assert isinstance(graph["nodes"], list) and isinstance(graph["edges"], list)
    # audit
    ra = client.get(f"/api/investigations/{inv_id}/audit")
    assert ra.status_code == 200
    audit = ra.json()
    assert "count" in audit and "events" in audit
    assert isinstance(audit["events"], list)
    assert audit["count"] >= len(audit["events"])
    # query endpoint
    rq = client.post("/api/query", json={"query": "pressure"})
    assert rq.status_code == 200
    qd = rq.json()
    assert "answer" in qd and "state" in qd and "contradictions" in qd
    assert qd["state"] in (
        "SUPPORTED",
        "PARTIALLY_SUPPORTED",
        "INSUFFICIENT_EVIDENCE",
        "CONFLICTING_EVIDENCE",
    )
    # Evidence chunk endpoint (pick any from evidence_refs if present)
    if inv.get("evidence_refs"):
        first = inv["evidence_refs"][0]
        cid = first.get("chunk_id")
        if cid:
            rc = client.get(f"/api/evidence/{cid}")
            assert rc.status_code == 200
            chunk = rc.json()
            # Backend returns row with id/text/doc_id/page_number/etc. — also has 'text'
            assert "text" in chunk and ("metadata" in chunk or "doc_id" in chunk)


def test_investigation_too_short():
    r = client.post("/api/investigations", json={"objective": "too short"})
    # API uses manual HTTPException(400) rather than pydantic; either is acceptable
    assert r.status_code in (400, 422)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])