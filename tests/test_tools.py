import tempfile
from pathlib import Path
import datetime
import uuid

from backend.app.ingestion.models import NormalizedDocument, Page
from backend.app.ingestion.chunker import chunk_document
from backend.app.retrieval.vector_store import ChromaStore
from backend.app.retrieval.embedder import MockEmbedder
from backend.app.retrieval.retriever import Retriever
from backend.app.store.db import get_connection, init_db, insert_sensor_event, insert_maintenance_log
from backend.app.tools.registry import list_tools, get_tool, execute_tool, TOOL_REGISTRY
from backend.app.tools.search_documents import SearchDocumentsInput
from backend.app.tools.retrieve_evidence import RetrieveEvidenceInput
from backend.app.tools.query_sensor_data import QuerySensorDataInput
from backend.app.tools.search_maintenance_logs import SearchMaintenanceLogsInput
from backend.app.tools.common import ToolOutput

# Helpers for search_documents
def make_search_setup(tmp_path: Path):
    vs_path = tmp_path / "vs"
    store = ChromaStore(persist_dir=vs_path)
    texts = ["Pump P-204 pressure 2.1 bar threshold", "Valve replaced 12 August maintenance log", "Vibration observed at 14:35"]
    docs = []
    for i, t in enumerate(texts):
        doc = NormalizedDocument(
            document_id=f"doc{i}",
            filename=f"f{i}.pdf" if i == 0 else f"f{i}.txt",
            file_type="txt",
            source_path=f"/tmp/f{i}.txt",
            sha256="abc123",
            title="t",
            ingested_at=datetime.datetime.now(datetime.timezone.utc),
            pages=[Page(page_number=1, text=t, char_count=len(t))],
        )
        docs.extend(chunk_document(doc))
    emb = MockEmbedder(dim=384)
    store.upsert(docs, emb.embed([c.text for c in docs]))
    retriever = Retriever(embedder=emb, vector_store=store)
    return retriever

# Helpers for SQLite seeded DB
def seed_sensor_and_maint(db_path: Path):
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        # sensor events
        for row in [
            {"id": "s1", "equipment_id": "P-204", "timestamp": "2026-08-15T08:00:00", "metric": "pressure_bar", "value": 2.3, "source_file": "sensor.csv", "doc_id": "doc1"},
            {"id": "s2", "equipment_id": "P-204", "timestamp": "2026-08-15T14:30:00", "metric": "pressure_bar", "value": 4.8, "source_file": "sensor.csv", "doc_id": "doc1"},
            {"id": "s3", "equipment_id": "P-205", "timestamp": "2026-08-15T08:00:00", "metric": "pressure_bar", "value": 1.9, "source_file": "sensor.csv", "doc_id": "doc2"},
        ]:
            insert_sensor_event(conn, row)
        for row in [
            {"id": "m1", "equipment_id": "P-204", "date": "2026-08-10", "action": "seal inspection", "technician": "Tech A", "source_file": "maintenance.csv", "doc_id": "doc1"},
            {"id": "m2", "equipment_id": "P-204", "date": "2026-08-12", "action": "valve replacement", "technician": "Tech B", "source_file": "maintenance.csv", "doc_id": "doc1"},
            {"id": "m3", "equipment_id": "P-205", "date": "2026-08-11", "action": "oil change", "technician": "Tech C", "source_file": "maintenance.csv", "doc_id": "doc2"},
        ]:
            insert_maintenance_log(conn, row)
        conn.commit()
    finally:
        conn.close()

# === search_documents valid/invalid ===
def test_search_documents_valid(tmp_path: Path):
    retr = make_search_setup(tmp_path)
    inp = SearchDocumentsInput(query="pressure", top_k=2)
    from backend.app.tools.search_documents import search_documents
    out = search_documents(inp, retriever=retr)
    assert out.success is True
    assert len(out.result) >= 1
    assert len(out.evidence_refs) == len(out.result)
    # provenance
    for ref in out.evidence_refs:
        assert ref.filename is not None
        assert ref.chunk_id is not None
        assert ref.sha256 == "abc123"

def test_search_documents_invalid_empty_query():
    try:
        SearchDocumentsInput(query="", top_k=2)
        assert False
    except Exception:
        pass

def test_search_documents_invalid_top_k():
    try:
        SearchDocumentsInput(query="hello", top_k=100)
        assert False
    except Exception:
        pass

def test_search_documents_empty_results(tmp_path: Path):
    # empty store
    store = ChromaStore(persist_dir=tmp_path / "vs_empty")
    retr = Retriever(embedder=MockEmbedder(dim=16), vector_store=store)
    from backend.app.tools.search_documents import search_documents
    out = search_documents(SearchDocumentsInput(query="anything", top_k=2), retriever=retr)
    assert out.success is True
    assert out.result == []
    assert out.evidence_refs == []

def test_search_documents_deterministic(tmp_path: Path):
    retr = make_search_setup(tmp_path)
    from backend.app.tools.search_documents import search_documents
    inp = SearchDocumentsInput(query="pressure")
    out1 = search_documents(inp, retriever=retr)
    out2 = search_documents(inp, retriever=retr)
    assert out1.result == out2.result
    assert [r.chunk_id for r in out1.evidence_refs] == [r.chunk_id for r in out2.evidence_refs]

def test_search_documents_filters(tmp_path: Path):
    retr = make_search_setup(tmp_path)
    from backend.app.tools.search_documents import search_documents
    out = search_documents(SearchDocumentsInput(query="pressure", top_k=5, filters={"filename": "f0.pdf"}), retriever=retr)
    assert all(r["filename"] == "f0.pdf" for r in out.result)

def test_search_documents_invalid_filter():
    try:
        SearchDocumentsInput(query="hello", filters={"bad": "x"})
        assert False
    except Exception:
        pass

# === retrieve_evidence ===
def test_retrieve_evidence_by_chunk_id(tmp_path: Path):
    db = tmp_path / "work.db"
    init_db(db)
    # create doc via direct insert
    from backend.app.ingestion.models import NormalizedDocument
    doc_id = "doc-retrieve-" + uuid.uuid4().hex[:6]
    doc = NormalizedDocument(
        document_id=doc_id,
        filename="test.pdf",
        file_type="pdf",
        source_path="/tmp/test.pdf",
        sha256="deadbeef",
        title="t",
        ingested_at=datetime.datetime.now(datetime.timezone.utc),
        pages=[Page(page_number=1, text="hello world chunk", char_count=17)],
    )
    chunks = chunk_document(doc)
    from backend.app.store.db import get_connection, insert_document, insert_chunks
    conn = get_connection(db)
    insert_document(conn, doc)
    insert_chunks(conn, chunks)
    conn.commit()
    conn.close()
    from backend.app.tools.retrieve_evidence import retrieve_evidence
    inp = RetrieveEvidenceInput(chunk_id=chunks[0].chunk_id)
    out = retrieve_evidence(inp, db_path=db)
    assert out.success is True
    assert out.result["chunk_id"] == chunks[0].chunk_id
    assert out.evidence_refs[0].chunk_id == chunks[0].chunk_id
    assert out.evidence_refs[0].sha256 == "deadbeef"
    assert out.evidence_refs[0].filename == "test.pdf"
    assert out.evidence_refs[0].page_number == 1

def test_retrieve_evidence_by_document_id(tmp_path: Path):
    db = tmp_path / "work2.db"
    init_db(db)
    doc_id = "doc2-" + uuid.uuid4().hex[:6]
    doc = NormalizedDocument(
        document_id=doc_id,
        filename="doc2.pdf",
        file_type="pdf",
        source_path="/tmp/doc2.pdf",
        sha256="abc",
        title="t",
        ingested_at=datetime.datetime.now(datetime.timezone.utc),
        pages=[Page(page_number=1, text="chunk one text here", char_count=18), Page(page_number=2, text="chunk two", char_count=9)],
    )
    chunks = chunk_document(doc)
    conn = get_connection(db)
    from backend.app.store.db import insert_document, insert_chunks
    insert_document(conn, doc)
    insert_chunks(conn, chunks)
    conn.commit()
    conn.close()
    from backend.app.tools.retrieve_evidence import retrieve_evidence
    out = retrieve_evidence(RetrieveEvidenceInput(document_id=doc_id), db_path=db)
    assert out.success is True
    assert out.result["document"]["id"] == doc_id
    assert len(out.result["chunks"]) == len(chunks)
    assert len(out.evidence_refs) == len(chunks)

def test_retrieve_evidence_invalid_input():
    try:
        RetrieveEvidenceInput()
        assert False
    except Exception:
        pass
    try:
        RetrieveEvidenceInput(chunk_id="bad id with spaces!")
        assert False
    except Exception:
        pass

def test_retrieve_evidence_not_found(tmp_path: Path):
    db = tmp_path / "empty.db"
    init_db(db)
    from backend.app.tools.retrieve_evidence import retrieve_evidence
    out = retrieve_evidence(RetrieveEvidenceInput(chunk_id="nonexistent:chunk:0000"), db_path=db)
    assert out.success is False
    assert "not found" in out.error.lower()

def test_retrieve_evidence_provenance(tmp_path: Path):
    db = tmp_path / "prov.db"
    init_db(db)
    doc = NormalizedDocument(
        document_id="doc-prov",
        filename="prov.pdf",
        file_type="pdf",
        source_path="/tmp/prov.pdf",
        sha256="ff00ff",
        title="t",
        ingested_at=datetime.datetime.now(datetime.timezone.utc),
        pages=[Page(page_number=2, text="provenance test", char_count=15)],
    )
    chunks = chunk_document(doc)
    conn = get_connection(db)
    from backend.app.store.db import insert_document, insert_chunks
    insert_document(conn, doc)
    insert_chunks(conn, chunks)
    conn.commit()
    conn.close()
    from backend.app.tools.retrieve_evidence import retrieve_evidence
    out = retrieve_evidence(RetrieveEvidenceInput(chunk_id=chunks[0].chunk_id), db_path=db)
    assert out.evidence_refs[0].filename == "prov.pdf"
    assert out.evidence_refs[0].page_number == 2
    assert out.evidence_refs[0].sha256 == "ff00ff"

# === query_sensor_data ===
def test_query_sensor_data_valid(tmp_path: Path):
    db = tmp_path / "sensor.db"
    seed_sensor_and_maint(db)
    from backend.app.tools.query_sensor_data import query_sensor_data
    out = query_sensor_data(QuerySensorDataInput(equipment_id="P-204"), db_path=db)
    assert out.success is True
    assert len(out.result) == 2
    assert all(r["equipment_id"] == "P-204" for r in out.result)
    assert len(out.evidence_refs) == 2
    assert out.evidence_refs[0].source_path == "sensor.csv"

def test_query_sensor_data_metric_filter(tmp_path: Path):
    db = tmp_path / "sensor2.db"
    seed_sensor_and_maint(db)
    from backend.app.tools.query_sensor_data import query_sensor_data
    out = query_sensor_data(QuerySensorDataInput(equipment_id="P-204", metric="pressure_bar"), db_path=db)
    assert len(out.result) == 2
    out2 = query_sensor_data(QuerySensorDataInput(equipment_id="P-204", metric="temperature"), db_path=db)
    assert len(out2.result) == 0
    assert out2.success is True

def test_query_sensor_data_time_filter(tmp_path: Path):
    db = tmp_path / "sensor3.db"
    seed_sensor_and_maint(db)
    from backend.app.tools.query_sensor_data import query_sensor_data
    out = query_sensor_data(
        QuerySensorDataInput(equipment_id="P-204", start_time=datetime.datetime.fromisoformat("2026-08-15T14:00:00"), end_time=datetime.datetime.fromisoformat("2026-08-15T15:00:00")),
        db_path=db,
    )
    assert len(out.result) == 1
    assert out.result[0]["value"] == 4.8

def test_query_sensor_data_aggregation(tmp_path: Path):
    db = tmp_path / "sensor4.db"
    seed_sensor_and_maint(db)
    from backend.app.tools.query_sensor_data import query_sensor_data
    out = query_sensor_data(QuerySensorDataInput(equipment_id="P-204", aggregation="avg"), db_path=db)
    assert out.success is True
    assert out.result["value"] == (2.3 + 4.8) / 2
    out2 = query_sensor_data(QuerySensorDataInput(equipment_id="P-204", aggregation="max"), db_path=db)
    assert out2.result["value"] == 4.8

def test_query_sensor_data_invalid_equipment():
    try:
        QuerySensorDataInput(equipment_id="P 204!")
        assert False
    except Exception:
        pass

def test_query_sensor_data_empty(tmp_path: Path):
    db = tmp_path / "empty_sensor.db"
    seed_sensor_and_maint(db)
    from backend.app.tools.query_sensor_data import query_sensor_data
    out = query_sensor_data(QuerySensorDataInput(equipment_id="UNKNOWN"), db_path=db)
    assert out.success is True
    assert out.result == []
    assert out.evidence_refs == []

def test_query_sensor_data_deterministic(tmp_path: Path):
    db = tmp_path / "det_sensor.db"
    seed_sensor_and_maint(db)
    from backend.app.tools.query_sensor_data import query_sensor_data
    inp = QuerySensorDataInput(equipment_id="P-204")
    out1 = query_sensor_data(inp, db_path=db)
    out2 = query_sensor_data(inp, db_path=db)
    assert out1.result == out2.result

# === search_maintenance_logs ===
def test_search_maintenance_valid(tmp_path: Path):
    db = tmp_path / "maint.db"
    seed_sensor_and_maint(db)
    from backend.app.tools.search_maintenance_logs import search_maintenance_logs
    out = search_maintenance_logs(SearchMaintenanceLogsInput(equipment_id="P-204"), db_path=db)
    assert out.success is True
    assert len(out.result) == 2
    assert len(out.evidence_refs) == 2

def test_search_maintenance_keyword(tmp_path: Path):
    db = tmp_path / "maint2.db"
    seed_sensor_and_maint(db)
    from backend.app.tools.search_maintenance_logs import search_maintenance_logs
    out = search_maintenance_logs(SearchMaintenanceLogsInput(equipment_id="P-204", keyword="valve"), db_path=db)
    assert len(out.result) == 1
    assert "valve" in out.result[0]["action"]

def test_search_maintenance_date_filter(tmp_path: Path):
    db = tmp_path / "maint3.db"
    seed_sensor_and_maint(db)
    from backend.app.tools.search_maintenance_logs import search_maintenance_logs
    out = search_maintenance_logs(SearchMaintenanceLogsInput(equipment_id="P-204", start_date=datetime.date.fromisoformat("2026-08-11"), end_date=datetime.date.fromisoformat("2026-08-13")), db_path=db)
    assert len(out.result) == 1
    assert out.result[0]["date"] == "2026-08-12"

def test_search_maintenance_invalid():
    try:
        SearchMaintenanceLogsInput(equipment_id="bad id!")
        assert False
    except Exception:
        pass

def test_search_maintenance_empty(tmp_path: Path):
    db = tmp_path / "maint_empty.db"
    seed_sensor_and_maint(db)
    from backend.app.tools.search_maintenance_logs import search_maintenance_logs
    out = search_maintenance_logs(SearchMaintenanceLogsInput(equipment_id="UNKNOWN"), db_path=db)
    assert out.success is True
    assert out.result == []

def test_search_maintenance_deterministic(tmp_path: Path):
    db = tmp_path / "maint_det.db"
    seed_sensor_and_maint(db)
    from backend.app.tools.search_maintenance_logs import search_maintenance_logs
    inp = SearchMaintenanceLogsInput(equipment_id="P-204")
    out1 = search_maintenance_logs(inp, db_path=db)
    out2 = search_maintenance_logs(inp, db_path=db)
    assert out1.result == out2.result

# === registry ===
def test_registry_list():
    tools = list_tools()
    assert "search_documents" in tools
    assert "retrieve_evidence" in tools
    assert "query_sensor_data" in tools
    assert "search_maintenance_logs" in tools
    assert "inspect_image" in tools
    assert len(tools) >= 5

def test_registry_get_valid():
    spec = get_tool("search_documents")
    assert "input_model" in spec and "handler" in spec

def test_registry_get_invalid():
    try:
        get_tool("unknown_tool")
        assert False
    except ValueError as e:
        assert "Unknown tool" in str(e)

def test_registry_execute_valid(tmp_path: Path):
    retr = make_search_setup(tmp_path)
    out = execute_tool("search_documents", {"query": "pressure", "top_k": 2}, retriever=retr)
    assert isinstance(out, ToolOutput)
    assert out.success is True

def test_registry_execute_invalid_input():
    try:
        execute_tool("search_documents", {"query": ""})
        assert False
    except Exception:
        pass

def test_registry_execute_unknown():
    try:
        execute_tool("bad_tool", {})
        assert False
    except ValueError:
        pass

def test_tools_no_network_import():
    # Ensure tools don't import httpx/requests/openai
    import ast, pathlib
    for p in pathlib.Path("backend/app/tools").glob("*.py"):
        src = p.read_text(encoding="utf-8")
        assert "import httpx" not in src
        assert "import requests" not in src
        assert "openai" not in src.lower()
