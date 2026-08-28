import tempfile
from pathlib import Path
import uuid
import datetime

from backend.app.ingestion.models import NormalizedDocument, Page
from backend.app.ingestion.chunker import chunk_document
from backend.app.retrieval.embedder import MockEmbedder
from backend.app.retrieval.vector_store import ChromaStore
from backend.app.retrieval.retriever import Retriever


def make_store_with_texts(texts: list[str], tmp_path: Path):
    store = ChromaStore(persist_dir=tmp_path / "vs")
    docs = []
    for i, t in enumerate(texts):
        doc = NormalizedDocument(
            document_id=f"doc{i}-{uuid.uuid4().hex[:4]}",
            filename=f"f{i}.txt",
            file_type="txt",
            source_path=f"/tmp/f{i}.txt",
            sha256="abc",
            title="t",
            ingested_at=datetime.datetime.now(datetime.timezone.utc),
            pages=[Page(page_number=1, text=t, char_count=len(t))],
        )
        chunks = chunk_document(doc)
        docs.extend(chunks)
    emb = MockEmbedder(dim=384)
    vecs = emb.embed([c.text for c in docs])
    store.upsert(docs, vecs)
    return store, emb


def test_retrieve_basic(tmp_path: Path):
    store, emb = make_store_with_texts(["Pump P-204 pressure 2.1 bar", "Valve replacement log", "Vibration observed"], tmp_path)
    retriever = Retriever(embedder=emb, vector_store=store)
    hits = retriever.retrieve("pressure threshold", top_k=3)
    assert len(hits) >= 1
    assert all(isinstance(h.score, float) for h in hits)
    assert all(h.metadata.get("filename") for h in hits)


def test_retrieve_threshold_filters(tmp_path: Path):
    store, emb = make_store_with_texts(["hello world", "pump pressure", "valve log"], tmp_path)
    retriever = Retriever(embedder=emb, vector_store=store)
    # high threshold should filter more (mock similarities may be negative)
    hits_low = retriever.retrieve("pressure", top_k=3, threshold=0.0)
    hits_high = retriever.retrieve("pressure", top_k=3, threshold=0.9)
    assert len(hits_high) <= len(hits_low)


def test_retrieve_metadata_filter(tmp_path: Path):
    store, emb = make_store_with_texts(["apple fruit", "engine pump"], tmp_path)
    retriever = Retriever(embedder=emb, vector_store=store)
    hits = retriever.retrieve("pump", top_k=5, filters={"filename": "f1.txt"})
    assert len(hits) >= 1
    assert all(h.metadata["filename"] == "f1.txt" for h in hits)


def test_retrieve_invalid_query(tmp_path: Path):
    store, emb = make_store_with_texts(["hello"], tmp_path)
    retriever = Retriever(embedder=emb, vector_store=store)
    try:
        retriever.retrieve("", top_k=3)
        assert False, "should raise"
    except ValueError:
        pass


def test_retrieve_invalid_filter(tmp_path: Path):
    store, emb = make_store_with_texts(["hello"], tmp_path)
    retriever = Retriever(embedder=emb, vector_store=store)
    try:
        retriever.retrieve("hello", filters={"bad": "x"})
        assert False
    except ValueError:
        pass


def test_retrieve_top_k_bounds(tmp_path: Path):
    store, emb = make_store_with_texts(["hello"], tmp_path)
    retriever = Retriever(embedder=emb, vector_store=store)
    try:
        retriever.retrieve("hello", top_k=0)
        assert False
    except ValueError:
        pass
    try:
        retriever.retrieve("hello", top_k=100)
        assert False
    except ValueError:
        pass


def test_retrieve_provenance(tmp_path: Path):
    store, emb = make_store_with_texts(["Pump P-204 SOP 2.1 bar"], tmp_path)
    retriever = Retriever(embedder=emb, vector_store=store)
    hits = retriever.retrieve("SOP pressure", top_k=1)
    assert hits[0].chunk_id
    assert hits[0].metadata["sha256"]
    assert hits[0].metadata["filename"]
    assert hits[0].distance is not None
