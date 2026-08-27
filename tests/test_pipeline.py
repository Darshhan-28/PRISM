import tempfile
from pathlib import Path

from backend.app.ingestion.pipeline import IngestionPipeline
from backend.app.config import reset_config


def test_pipeline_ingest_all_types_with_mock_embed(tmp_path: Path):
    # Setup isolated dirs
    raw = tmp_path / "raw"
    processed = tmp_path / "processed"
    vs = tmp_path / "vs"
    db = tmp_path / "workbench.db"
    raw.mkdir()

    fixtures = Path(__file__).parent / "fixtures"
    for p in fixtures.glob("*"):
        if p.is_file() and p.suffix.lower() in (".pdf", ".csv", ".json", ".txt", ".log", ".md"):
            (raw / p.name).write_bytes(p.read_bytes())

    # Override config via env not needed — pass explicit paths to pipeline
    pipeline = IngestionPipeline(processed_dir=processed, db_path=db)
    # Monkey patch config dirs for vector store
    from backend.app.config import get_config

    cfg = get_config()
    old_vs = cfg.vector_store_dir
    cfg.vector_store_dir = vs
    old_db = cfg.db_path
    cfg.db_path = db
    # ensure embedding_provider is mock
    old_prov = cfg.embedding_provider
    cfg.embedding_provider = "mock"
    try:
        report = pipeline.ingest_directory(raw, recursive=False, embed_and_store=True)
        assert report.ingested >= 6, f"expected >=6 ingested, got {report.per_file}"
        assert report.failed == 0
        assert report.total_chunks > 0
        # Check processed files exist
        json_files = list(processed.glob("*.json"))
        assert len(json_files) >= 6
        # vector store count matches total_chunks
        from backend.app.retrieval.vector_store import ChromaStore
        from backend.app.retrieval.embedder import MockEmbedder

        store = ChromaStore(persist_dir=vs)
        assert store.count() == report.total_chunks
        # provenance check: search returns metadata with sha256
        emb = MockEmbedder()
        qvec = emb.embed(["pressure 2.1 bar"])[0]
        hits = store.search(qvec, top_k=3)
        assert len(hits) >= 1
        assert "sha256" in hits[0]["metadata"]
        assert hits[0]["metadata"]["sha256"]
        # per-file isolation: bad file does not abort
        bad = raw / "bad.exe"
        bad.write_bytes(b"not allowed")
        report2 = pipeline.ingest_files([bad], embed_and_store=False)
        assert report2.per_file[0].status == "rejected"
        assert report2.per_file[0].error_code == "ALLOWLIST_REJECT"
    finally:
        cfg.vector_store_dir = old_vs
        cfg.db_path = old_db
        cfg.embedding_provider = old_prov


def test_pipeline_no_embed(tmp_path: Path):
    raw = tmp_path / "raw2"
    processed = tmp_path / "processed2"
    db = tmp_path / "db2.db"
    raw.mkdir()
    p = raw / "hello.txt"
    p.write_text("hello world")
    pipeline = IngestionPipeline(processed_dir=processed, db_path=db)
    report = pipeline.ingest_files([p], embed_and_store=False)
    assert report.ingested == 1
    assert report.total_chunks >= 1
    assert (processed / f"{report.per_file[0].document_id}.json").exists()
