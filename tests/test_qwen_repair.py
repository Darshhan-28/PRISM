"""Regression tests for Qwen 1.5B citation failures — deterministic repair."""
from backend.app.retrieval.retriever import RetrievedChunk
from backend.app.evidence.engine import build_grounded_prompt, repair_missing_citations, evaluate, answer_with_evidence, EvidenceState
from backend.app.retrieval.vector_store import ChromaStore
from backend.app.retrieval.embedder import MockEmbedder
from backend.app.retrieval.retriever import Retriever

def _chunk(cid, fname, text, score=0.9):
    return RetrievedChunk(chunk_id=cid, text=text, score=score, distance=0.1, metadata={"filename": fname, "chunk_id": cid, "document_id": cid.split(":")[0], "sha256": "abc", "source_path": f"data/raw/samples/{fname}"})

def test_build_grounded_prompt_explicit():
    chs = [_chunk("a:p1:c0", "SOP_P-204.pdf", "SOP limit 2.1-3.4"), _chunk("b:p1:c0", "sensor_P-204_2026-08-15.json", "sensor 4.8")]
    sys, prompt = build_grounded_prompt("query", chs)
    assert "STRICT RULES" in sys
    assert "[SOP_P-204.pdf]" in sys
    assert "[sensor_P-204_2026-08-15.json]" in sys
    assert "Example:" in sys
    assert "Answer (each sentence must end with a valid citation" in prompt

def test_repair_single_claim_missing():
    chs = [_chunk("a:p1:c0", "SOP_P-204.pdf", "SOP limit 2.1-3.4 bar")]
    ans = "SOP limit is 2.1-3.4 bar."
    repaired = repair_missing_citations(ans, chs)
    assert "[SOP_P-204.pdf]" in repaired
    # evaluate should now be SUPPORTED
    res = evaluate("q", chs, repaired)
    assert res.state == EvidenceState.SUPPORTED

def test_repair_preserves_insufficient():
    chs = [_chunk("a:p1:c0", "SOP_P-204.pdf", "text")]
    ans = "Insufficient evidence: cannot answer from provided evidence"
    repaired = repair_missing_citations(ans, chs)
    assert repaired == ans  # not repaired
    res = evaluate("q", chs, repaired)
    assert res.state == EvidenceState.INSUFFICIENT_EVIDENCE

def test_repair_multi_claim():
    chs = [_chunk("a:p1:c0", "SOP_P-204.pdf", "SOP 2.1"), _chunk("b:p1:c0", "sensor_P-204_2026-08-15.json", "sensor 4.8")]
    ans = "SOP limit is 2.1-3.4 bar. Sensor spiked to 4.8 bar"
    repaired = repair_missing_citations(ans, chs)
    # both claims should now have citations
    assert repaired.count("[") >= 2
    res = evaluate("q", chs, repaired)
    assert res.state == EvidenceState.SUPPORTED

def test_repair_no_invent():
    chs = [_chunk("a:p1:c0", "SOP_P-204.pdf", "SOP")]
    ans = "Answer without citation"
    repaired = repair_missing_citations(ans, chs)
    # must use actual filename, not hallucinated
    assert "[SOP_P-204.pdf]" in repaired
    assert "invented" not in repaired.lower()

def test_answer_with_evidence_qwen_uncited():
    # Simulate Qwen returning uncited answer via mock adapter
    from backend.app.llm.mock_adapter import MockAdapter
    from pathlib import Path
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    store = ChromaStore(persist_dir=tmp / "vs")
    # seed one doc
    from backend.app.ingestion.models import NormalizedDocument, Page
    from backend.app.ingestion.chunker import chunk_document
    import datetime
    doc = NormalizedDocument(document_id="doc0", filename="SOP_P-204.pdf", file_type="txt", source_path="data/raw/samples/SOP_P-204.pdf", sha256="abc", title="t", ingested_at=datetime.datetime.now(datetime.timezone.utc), pages=[Page(page_number=1, text="SOP limit 2.1-3.4 bar for P-204", char_count=30)])
    chunks = chunk_document(doc)
    emb = MockEmbedder(dim=8)
    store.upsert(chunks, emb.embed([c.text for c in chunks]))
    retr = Retriever(embedder=emb, vector_store=store)
    # Mock returns uncited answer (Qwen-style)
    mock = MockAdapter(canned={"Query:": "SOP limit is 2.1-3.4 bar."})  # no citation
    res = answer_with_evidence("What is SOP limit?", retr, mock)
    # repair should have made it SUPPORTED (since evidence exists and citations added deterministically)
    assert res.state == EvidenceState.SUPPORTED
    assert any("SOP_P-204.pdf" in c for c in res.citations_found)

def test_conflicting_precedence():
    chs = [_chunk("a:p1:c0", "maintenance_log_P-204.csv", "valve replaced"), _chunk("b:p1:c0", "checklist_P-204.md", "valve pending")]
    ans = "Valve was replaced [maintenance_log_P-204.csv]"
    res = evaluate("q", chs, ans)
    assert res.state == EvidenceState.CONFLICTING_EVIDENCE
    # even after repair, conflicting remains
    repaired = repair_missing_citations("Valve status unknown", chs)
    res2 = evaluate("q", chs, repaired)
    assert res2.state == EvidenceState.CONFLICTING_EVIDENCE
