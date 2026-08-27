import uuid
import datetime
from pathlib import Path

from backend.app.ingestion.models import NormalizedDocument, Page
from backend.app.ingestion.chunker import chunk_document
from backend.app.retrieval.embedder import MockEmbedder
from backend.app.retrieval.vector_store import ChromaStore


def make_doc_with_chunks(text: str, filename: str = "test.txt"):
    doc = NormalizedDocument(
        document_id=uuid.uuid4().hex,
        filename=filename,
        file_type="txt",
        source_path=f"/tmp/{filename}",
        sha256="abc123",
        title="t",
        ingested_at=datetime.datetime.now(datetime.timezone.utc),
        pages=[Page(page_number=1, text=text, char_count=len(text))],
    )
    chunks = chunk_document(doc, chunk_size=200, chunk_overlap=20)
    return doc, chunks


def test_upsert_count_search(tmp_path: Path):
    store = ChromaStore(persist_dir=tmp_path / "vs")
    doc, chunks = make_doc_with_chunks("Pump P-204 pressure threshold 2.1 to 3.4 bar normal operation.")
    emb = MockEmbedder(dim=384)
    vecs = emb.embed([c.text for c in chunks])
    store.upsert(chunks, vecs)
    assert store.count() == len(chunks)
    qvec = emb.embed(["pressure threshold"])[0]
    hits = store.search(qvec, top_k=3)
    assert len(hits) >= 1
    assert hits[0]["metadata"]["filename"] == "test.txt"


def test_delete(tmp_path: Path):
    store = ChromaStore(persist_dir=tmp_path / "vs2")
    doc, chunks = make_doc_with_chunks("hello world pump", filename="a.txt")
    vecs = MockEmbedder(dim=32).embed([c.text for c in chunks])
    store.upsert(chunks, vecs)
    assert store.count() == len(chunks)
    deleted = store.delete(doc.document_id)
    assert deleted == len(chunks)
    assert store.count() == 0


def test_filter(tmp_path: Path):
    store = ChromaStore(persist_dir=tmp_path / "vs3")
    doc1, ch1 = make_doc_with_chunks("apple fruit", filename="apple.txt")
    doc2, ch2 = make_doc_with_chunks("engine pump", filename="pump.txt")
    emb = MockEmbedder(dim=32)
    store.upsert(ch1, emb.embed([c.text for c in ch1]))
    store.upsert(ch2, emb.embed([c.text for c in ch2]))
    qvec = emb.embed(["pump"])[0]
    hits = store.search(qvec, top_k=5, filters={"filename": "pump.txt"})
    assert all(h["metadata"]["filename"] == "pump.txt" for h in hits)
