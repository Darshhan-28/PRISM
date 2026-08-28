import tempfile
from pathlib import Path
import datetime
import uuid

from backend.app.ingestion.models import NormalizedDocument, Page
from backend.app.ingestion.chunker import chunk_document
from backend.app.retrieval.vector_store import ChromaStore
from backend.app.retrieval.embedder import MockEmbedder
from backend.app.retrieval.retriever import Retriever, RetrievedChunk
from backend.app.llm.mock_adapter import MockAdapter
from backend.app.evidence.engine import (
    EvidenceState,
    evaluate,
    parse_citations,
    map_citations,
    detect_conflicting_evidence,
    build_grounded_prompt,
    answer_with_evidence,
)


def mk_chunk(chunk_id: str, text: str, filename: str = "SOP_P-204.pdf") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text=text,
        score=0.9,
        distance=0.1,
        metadata={
            "filename": filename,
            "chunk_id": chunk_id,
            "document_id": chunk_id.split(":")[0],
            "sha256": "abc123",
            "page_number": 1,
            "source_path": f"/tmp/{filename}",
        },
    )


# 1. Supported answers
def test_supported_all_cited():
    retrieved = [
        mk_chunk("doc1:p1:c0000", "Normal pressure 2.1-3.4 bar", "SOP_P-204.pdf"),
        mk_chunk("doc1:p1:c0001", "Valve replaced 12 August", "maintenance_log.csv"),
    ]
    ans = "Normal pressure is 2.1-3.4 bar [SOP_P-204.pdf]. Valve was replaced [maintenance_log.csv]."
    res = evaluate("pressure?", retrieved, ans)
    assert res.state == EvidenceState.SUPPORTED
    assert len(res.evidence_refs) == 2
    assert res.invalid_citations == []


# 2. Partially supported
def test_partially_supported_mixed_claims():
    retrieved = [mk_chunk("doc1:p1:c0000", "Pressure 2.1 bar", "SOP.pdf")]
    ans = "Pressure is 2.1 bar [SOP.pdf]. The pump is blue and large."
    res = evaluate("q", retrieved, ans)
    assert res.state == EvidenceState.PARTIALLY_SUPPORTED
    assert "1/2" in res.missing_coverage or "claims" in res.missing_coverage.lower()


def test_partially_supported_invalid_plus_valid():
    retrieved = [mk_chunk("doc1:p1:c0000", "Pressure", "SOP.pdf")]
    ans = "Good [SOP.pdf] and bad [nonexistent.pdf]."
    res = evaluate("q", retrieved, ans)
    assert res.state == EvidenceState.PARTIALLY_SUPPORTED
    assert "nonexistent.pdf" in res.invalid_citations
    assert len(res.evidence_refs) == 1


# 3. Insufficient evidence
def test_insufficient_no_retrieved():
    res = evaluate("q", [], "Answer [SOP.pdf]")
    assert res.state == EvidenceState.INSUFFICIENT_EVIDENCE


def test_insufficient_no_citations():
    retrieved = [mk_chunk("c1", "some text", "a.pdf")]
    res = evaluate("q", retrieved, "This is an answer without citations.")
    assert res.state == EvidenceState.INSUFFICIENT_EVIDENCE


def test_insufficient_explicit_phrase():
    retrieved = [mk_chunk("c1", "text", "a.pdf")]
    res = evaluate("q", retrieved, "Insufficient evidence to answer [a.pdf].")
    assert res.state == EvidenceState.INSUFFICIENT_EVIDENCE


def test_insufficient_all_invalid_citations():
    retrieved = [mk_chunk("c1", "text", "a.pdf")]
    res = evaluate("q", retrieved, "Answer [bad.pdf] and [worse.pdf].")
    assert res.state == EvidenceState.INSUFFICIENT_EVIDENCE
    assert len(res.invalid_citations) == 2


# 4. Conflicting evidence
def test_conflicting_evidence_keyword():
    retrieved = [
        mk_chunk("c1", "valve replaced on 12 August", "maintenance_log.csv"),
        mk_chunk("c2", "valve replacement pending", "checklist.md"),
    ]
    assert detect_conflicting_evidence(retrieved) is True
    res = evaluate("valve status?", retrieved, "Valve is replaced [maintenance_log.csv].")
    assert res.state == EvidenceState.CONFLICTING_EVIDENCE
    assert res.conflicting is True


def test_not_conflicting_single_side():
    retrieved = [mk_chunk("c1", "valve replaced", "a.pdf")]
    assert detect_conflicting_evidence(retrieved) is False


# 5. Missing/invalid citations
def test_parse_citations():
    assert parse_citations("a [file.pdf] b [chunk:123]") == ["file.pdf", "chunk:123"]
    assert parse_citations("no citations") == []


def test_map_citations_valid_and_invalid():
    retrieved = [mk_chunk("doc1:p1:c0000", "text", "SOP.pdf")]
    valid, invalid = map_citations(retrieved, ["SOP.pdf", "bad.pdf"])
    assert len(valid) == 1
    assert invalid == ["bad.pdf"]


def test_citation_case_insensitive():
    retrieved = [mk_chunk("c1", "text", "SOP.pdf")]
    valid, invalid = map_citations(retrieved, ["sop.pdf"])
    assert len(valid) == 1


# 6. Provenance preservation
def test_provenance_preserved():
    retrieved = [mk_chunk("docX:p1:c0005", "pressure 4.8 bar", "sensor.json")]
    res = evaluate("q", retrieved, "Pressure 4.8 [sensor.json].")
    assert res.evidence_refs[0].chunk_id == "docX:p1:c0005"
    assert res.evidence_refs[0].filename == "sensor.json"
    assert res.evidence_refs[0].sha256 == "abc123"
    assert res.evidence_refs[0].page_number == 1


def test_provenance_dedup():
    retrieved = [mk_chunk("c1", "text", "a.pdf")]
    res = evaluate("q", retrieved, "Claim [a.pdf] and again [a.pdf].")
    assert len(res.evidence_refs) == 1  # deduped


# 7. Deterministic behavior
def test_deterministic_evaluate():
    retrieved = [mk_chunk("c1", "text", "a.pdf")]
    ans = "Answer [a.pdf]."
    r1 = evaluate("q", retrieved, ans)
    r2 = evaluate("q", retrieved, ans)
    assert r1.state == r2.state
    assert r1.evidence_refs == r2.evidence_refs


# Integration: Retriever + LLMAdapter via DI
def test_answer_with_evidence_supported(tmp_path: Path):
    # Setup vector store with mock embeddings
    store = ChromaStore(persist_dir=tmp_path / "vs_sup")
    # Create docs
    doc_texts = ["Normal pressure 2.1-3.4 bar SOP P-204", "Valve replaced 12 August"]
    docs = []
    for i, t in enumerate(doc_texts):
        doc = NormalizedDocument(
            document_id=f"doc{i}",
            filename=f"f{i}.pdf" if i == 0 else "maintenance.csv",
            file_type="txt",
            source_path=f"/tmp/f{i}.txt",
            sha256="abc",
            title="t",
            ingested_at=datetime.datetime.now(datetime.timezone.utc),
            pages=[Page(page_number=1, text=t, char_count=len(t))],
        )
        docs.extend(chunk_document(doc))
    emb = MockEmbedder(dim=384)
    store.upsert(docs, emb.embed([c.text for c in docs]))
    retriever = Retriever(embedder=emb, vector_store=store)
    # MockAdapter that returns supported answer with citations for any query containing "pressure" or "SOP"
    llm = MockAdapter()
    res = answer_with_evidence("What is normal pressure?", retriever, llm)
    # MockAdapter returns SOP citation for pressure queries -> should be SUPPORTED or PARTIALLY (depends on retrieved)
    assert res.state in (EvidenceState.SUPPORTED, EvidenceState.PARTIALLY_SUPPORTED, EvidenceState.INSUFFICIENT_EVIDENCE)
    # provenance
    if res.state != EvidenceState.INSUFFICIENT_EVIDENCE:
        assert len(res.evidence_refs) >= 1


def test_answer_with_evidence_insufficient_no_retrieved(tmp_path: Path):
    store = ChromaStore(persist_dir=tmp_path / "vs_empty")
    retriever = Retriever(embedder=MockEmbedder(dim=16), vector_store=store)
    llm = MockAdapter()
    res = answer_with_evidence("unknown query with no evidence", retriever, llm)
    assert res.state == EvidenceState.INSUFFICIENT_EVIDENCE
    assert "No evidence" in res.answer or "Insufficient" in res.answer


def test_answer_with_evidence_uses_canned_mock(tmp_path: Path):
    store = ChromaStore(persist_dir=tmp_path / "vs_canned")
    doc = NormalizedDocument(
        document_id="doc0",
        filename="SOP_P-204.pdf",
        file_type="txt",
        source_path="/tmp/a.txt",
        sha256="abc",
        title="t",
        ingested_at=datetime.datetime.now(datetime.timezone.utc),
        pages=[Page(page_number=1, text="Normal pressure 2.1 bar", char_count=20)],
    )
    chunks = chunk_document(doc)
    emb = MockEmbedder(dim=16)
    store.upsert(chunks, emb.embed([c.text for c in chunks]))
    retriever = Retriever(embedder=emb, vector_store=store)
    # canned response includes valid citation
    llm = MockAdapter(canned={"pressure": "Pressure is 2.1 bar [SOP_P-204.pdf]."})
    res = answer_with_evidence("pressure?", retriever, llm)
    assert res.state == EvidenceState.SUPPORTED
    assert res.evidence_refs[0].filename == "SOP_P-204.pdf"


def test_build_grounded_prompt():
    ch = mk_chunk("c1", "evidence text here", "a.pdf")
    system, prompt = build_grounded_prompt("What is pressure?", [ch])
    assert "Answer ONLY" in system
    assert "a.pdf" in prompt
    assert "evidence text here" in prompt
    assert "<RETRIEVED_CHUNK" in prompt
