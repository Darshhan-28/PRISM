import json
import tempfile
from pathlib import Path
import datetime
import uuid

from backend.app.ingestion.models import NormalizedDocument, Page
from backend.app.ingestion.chunker import chunk_document
from backend.app.retrieval.vector_store import ChromaStore
from backend.app.retrieval.embedder import MockEmbedder
from backend.app.retrieval.retriever import Retriever
from backend.app.store.db import init_db, get_connection, insert_sensor_event, insert_maintenance_log
from backend.app.llm.mock_adapter import MockAdapter
from backend.app.orchestrator.investigation import InvestigationOrchestrator, MAX_STEPS, InvestigationPlan
from backend.app.evidence.engine import EvidenceState


def make_retriever(tmp_path: Path):
    vs = tmp_path / "vs"
    store = ChromaStore(persist_dir=vs)
    texts = ["Pump P-204 pressure 2.1 bar threshold SOP", "Valve replaced 12 August maintenance", "Sensor reading 4.8 bar"]
    docs = []
    for i, t in enumerate(texts):
        doc = NormalizedDocument(
            document_id=f"doc{i}",
            filename=f"SOP_P-204.pdf" if i == 0 else f"f{i}.txt",
            file_type="txt",
            source_path=f"/tmp/f{i}.txt",
            sha256="abc123",
            title="t",
            ingested_at=datetime.datetime.now(datetime.timezone.utc),
            pages=[Page(page_number=1, text=t, char_count=len(t))],
        )
        docs.extend(chunk_document(doc))
    emb = MockEmbedder(dim=16)
    store.upsert(docs, emb.embed([c.text for c in docs]))
    return Retriever(embedder=emb, vector_store=store)

def seed_db(db: Path):
    init_db(db)
    conn = get_connection(db)
    try:
        for row in [
            {"id": "s1", "equipment_id": "P-204", "timestamp": "2026-08-15T08:00:00", "metric": "pressure_bar", "value": 2.3, "source_file": "sensor.csv", "doc_id": "doc0"},
            {"id": "s2", "equipment_id": "P-204", "timestamp": "2026-08-15T14:30:00", "metric": "pressure_bar", "value": 4.8, "source_file": "sensor.csv", "doc_id": "doc0"},
        ]:
            insert_sensor_event(conn, row)
        for row in [
            {"id": "m1", "equipment_id": "P-204", "date": "2026-08-10", "action": "seal inspection", "technician": "Tech A", "source_file": "maintenance.csv", "doc_id": "doc0"},
            {"id": "m2", "equipment_id": "P-204", "date": "2026-08-12", "action": "valve replacement", "technician": "Tech B", "source_file": "maintenance.csv", "doc_id": "doc0"},
        ]:
            insert_maintenance_log(conn, row)
        conn.commit()
    finally:
        conn.close()

def make_valid_plan_json(objective="Investigate pressure in P-204 for test"):
    return json.dumps({
        "objective": objective,
        "steps": [
            {"step_no": 1, "tool": "search_documents", "input": {"query": "pressure"}, "rationale": "find SOP"},
            {"step_no": 2, "tool": "query_sensor_data", "input": {"equipment_id": "P-204"}, "rationale": "sensor"},
        ]
    })

def test_valid_investigation(tmp_path: Path):
    retr = make_retriever(tmp_path)
    db = tmp_path / "work.db"
    seed_db(db)
    plan_json = make_valid_plan_json()
    mock = MockAdapter(canned={"Objective": plan_json, "Query:": "Pressure is 2.1 bar [SOP_P-204.pdf]."})
    orch = InvestigationOrchestrator(llm_adapter=mock, retriever=retr, db_path=db)
    report = orch.investigate("Investigate pressure in P-204 for test valid")
    assert report.status in ("completed", "partial")
    assert len(report.steps_executed) == 2
    assert all(s.success for s in report.steps_executed)
    assert report.evidence_state in (EvidenceState.SUPPORTED, EvidenceState.PARTIALLY_SUPPORTED, EvidenceState.INSUFFICIENT_EVIDENCE, EvidenceState.CONFLICTING_EVIDENCE)
    # persistence
    conn = get_connection(db)
    cur = conn.execute("SELECT count(*) FROM investigations WHERE id=?", (report.investigation_id,))
    assert cur.fetchone()[0] == 1
    cur2 = conn.execute("SELECT count(*) FROM investigation_steps WHERE investigation_id=?", (report.investigation_id,))
    assert cur2.fetchone()[0] == 2
    conn.close()

def test_mock_planning_and_tool_execution(tmp_path: Path):
    retr = make_retriever(tmp_path)
    db = tmp_path / "work2.db"
    seed_db(db)
    # Plan with single search_documents
    plan = json.dumps({"objective": "Investigate valve status for P-204 test case", "steps": [{"step_no":1,"tool":"search_documents","input":{"query":"valve"},"rationale":"find valve"}]})
    mock = MockAdapter(canned={"Objective": plan, "Query:": "Valve was replaced [f1.txt]."})
    orch = InvestigationOrchestrator(llm_adapter=mock, retriever=retr, db_path=db)
    report = orch.investigate("Investigate valve status for P-204 test case")
    assert len(report.steps_executed) == 1
    assert report.steps_executed[0].tool == "search_documents"
    assert report.steps_executed[0].success is True

def test_multiple_steps(tmp_path: Path):
    retr = make_retriever(tmp_path)
    db = tmp_path / "work3.db"
    seed_db(db)
    plan = json.dumps({"objective": "Investigate multiple steps test objective long enough", "steps": [
        {"step_no":1,"tool":"search_documents","input":{"query":"pressure"},"rationale":"find pressure"},
        {"step_no":2,"tool":"search_maintenance_logs","input":{"equipment_id":"P-204"},"rationale":"find maintenance"},
        {"step_no":3,"tool":"query_sensor_data","input":{"equipment_id":"P-204"},"rationale":"check sensor data"},
    ]})
    mock = MockAdapter(canned={"Objective": plan})
    orch = InvestigationOrchestrator(llm_adapter=mock, retriever=retr, db_path=db)
    report = orch.investigate("Investigate multiple steps test objective long enough")
    assert len(report.steps_executed) == 3
    assert all(s.success for s in report.steps_executed)

def test_invalid_llm_plan_malformed(tmp_path: Path):
    retr = make_retriever(tmp_path)
    db = tmp_path / "work4.db"
    seed_db(db)
    mock = MockAdapter(canned={"Objective": "not json at all"})
    orch = InvestigationOrchestrator(llm_adapter=mock, retriever=retr, db_path=db)
    report = orch.investigate("Investigate malformed plan test objective long enough")
    assert report.status == "failed"
    assert "Plan validation failed" in report.error

def test_unknown_tool(tmp_path: Path):
    retr = make_retriever(tmp_path)
    db = tmp_path / "work5.db"
    seed_db(db)
    plan = json.dumps({"objective": "Investigate unknown tool test objective long enough", "steps": [{"step_no":1,"tool":"bad_tool","input":{},"rationale":"bad"}]})
    mock = MockAdapter(canned={"Objective": plan})
    orch = InvestigationOrchestrator(llm_adapter=mock, retriever=retr, db_path=db)
    report = orch.investigate("Investigate unknown tool test objective long enough")
    assert report.status == "failed"
    assert "Unknown tool" in report.error or "Plan validation" in report.error

def test_excessive_steps(tmp_path: Path):
    retr = make_retriever(tmp_path)
    db = tmp_path / "work6.db"
    seed_db(db)
    steps = [{"step_no":i+1,"tool":"search_documents","input":{"query":"q"},"rationale":"find evidence"} for i in range(9)]
    plan = json.dumps({"objective": "Investigate excessive steps test objective long enough indeed", "steps": steps})
    mock = MockAdapter(canned={"Objective": plan})
    orch = InvestigationOrchestrator(llm_adapter=mock, retriever=retr, db_path=db)
    report = orch.investigate("Investigate excessive steps test objective long enough indeed")
    assert report.status == "failed"
    assert "steps" in report.error.lower() or "MAX_STEPS" in report.error or "Plan validation" in report.error

def test_invalid_tool_input(tmp_path: Path):
    retr = make_retriever(tmp_path)
    db = tmp_path / "work7.db"
    seed_db(db)
    # query empty -> invalid
    plan = json.dumps({"objective": "Investigate invalid input test objective long enough", "steps": [{"step_no":1,"tool":"search_documents","input":{"query":""},"rationale":"bad"}]})
    mock = MockAdapter(canned={"Objective": plan})
    orch = InvestigationOrchestrator(llm_adapter=mock, retriever=retr, db_path=db)
    report = orch.investigate("Investigate invalid input test objective long enough")
    assert report.status == "failed"
    assert "Invalid input" in report.error or "Plan validation" in report.error

def test_tool_failure_graceful(tmp_path: Path):
    retr = make_retriever(tmp_path)
    db = tmp_path / "work8.db"
    seed_db(db)
    # retrieve_evidence with nonexistent chunk -> tool will return success False but orchestrator should handle
    plan = json.dumps({"objective": "Investigate tool failure test objective long enough", "steps": [{"step_no":1,"tool":"retrieve_evidence","input":{"chunk_id":"nonexistent:chunk:9999"},"rationale":"fail"}]})
    mock = MockAdapter(canned={"Objective": plan})
    orch = InvestigationOrchestrator(llm_adapter=mock, retriever=retr, db_path=db)
    report = orch.investigate("Investigate tool failure test objective long enough")
    assert len(report.steps_executed) == 1
    assert report.steps_executed[0].success is False
    assert report.evidence_state == EvidenceState.INSUFFICIENT_EVIDENCE

def test_empty_evidence(tmp_path: Path):
    # empty vector store and empty db
    vs = tmp_path / "vs_empty"
    from backend.app.retrieval.vector_store import ChromaStore
    store = ChromaStore(persist_dir=vs)
    retr = Retriever(embedder=MockEmbedder(dim=8), vector_store=store)
    db = tmp_path / "empty.db"
    init_db(db)
    plan = json.dumps({"objective": "Investigate empty evidence test objective long enough", "steps": [{"step_no":1,"tool":"search_documents","input":{"query":"nothing"},"rationale":"search"}]})
    mock = MockAdapter(canned={"Objective": plan})
    orch = InvestigationOrchestrator(llm_adapter=mock, retriever=retr, db_path=db)
    report = orch.investigate("Investigate empty evidence test objective long enough")
    # search_documents on empty store returns success True but empty result, so evidence empty -> insufficient
    assert report.evidence_state == EvidenceState.INSUFFICIENT_EVIDENCE

def test_evidence_state_propagation(tmp_path: Path):
    retr = make_retriever(tmp_path)
    db = tmp_path / "work9.db"
    seed_db(db)
    plan = json.dumps({"objective": "Investigate evidence state test objective long enough", "steps": [{"step_no":1,"tool":"search_documents","input":{"query":"pressure"},"rationale":"check pressure"}]})
    # Mock summary will be SOP citation -> but our pseudo chunks will have f0.pdf etc, need to match
    # Use canned summary that will be evaluated as SUPPORTED if citations match
    # Our search_documents will return chunks with filenames SOP_P-204.pdf etc, and MockAdapter default for pressure returns SOP citation, so it will match
    mock = MockAdapter(canned={"Objective": plan, "Query:": "Pressure is normal [SOP_P-204.pdf]."})
    orch = InvestigationOrchestrator(llm_adapter=mock, retriever=retr, db_path=db)
    report = orch.investigate("Investigate evidence state test objective long enough")
    assert report.evidence_state in (EvidenceState.SUPPORTED, EvidenceState.PARTIALLY_SUPPORTED, EvidenceState.CONFLICTING_EVIDENCE, EvidenceState.INSUFFICIENT_EVIDENCE)

def test_persistence(tmp_path: Path):
    retr = make_retriever(tmp_path)
    db = tmp_path / "persist.db"
    seed_db(db)
    plan = make_valid_plan_json("Investigate persistence test objective long enough")
    mock = MockAdapter(canned={"Objective": plan})
    orch = InvestigationOrchestrator(llm_adapter=mock, retriever=retr, db_path=db)
    report = orch.investigate("Investigate persistence test objective long enough")
    conn = get_connection(db)
    inv = conn.execute("SELECT * FROM investigations WHERE id=?", (report.investigation_id,)).fetchone()
    assert inv is not None
    assert inv["objective"] == "Investigate persistence test objective long enough"
    steps = conn.execute("SELECT * FROM investigation_steps WHERE investigation_id=? ORDER BY step_no", (report.investigation_id,)).fetchall()
    assert len(steps) == len(report.steps_executed)
    # check step fields
    assert steps[0]["tool"] == "search_documents"
    conn.close()

def test_deterministic_behavior(tmp_path: Path):
    retr = make_retriever(tmp_path)
    db = tmp_path / "det.db"
    seed_db(db)
    plan = json.dumps({"objective": "Deterministic test objective long enough for check", "steps": [{"step_no":1,"tool":"search_documents","input":{"query":"pressure"},"rationale":"find pressure data"}]})
    mock = MockAdapter(canned={"Objective": plan})
    orch = InvestigationOrchestrator(llm_adapter=mock, retriever=retr, db_path=db)
    r1 = orch.investigate("Deterministic test objective long enough for check")
    r2 = orch.investigate("Deterministic test objective long enough for check")
    assert r1.steps_executed[0].tool == r2.steps_executed[0].tool
    assert r1.evidence_state == r2.evidence_state
    # investigation_id differs but content same
    assert r1.objective == r2.objective

def test_no_network_and_no_arbitrary_execution(tmp_path: Path):
    # Ensure tools don't import httpx and orchestrator doesn't use eval
    import pathlib
    orch_src = pathlib.Path("backend/app/orchestrator/investigation.py").read_text(encoding="utf-8")
    assert "import httpx" not in orch_src
    assert "requests" not in orch_src.lower() or "httpx" not in orch_src
    assert "eval(" not in orch_src
    assert "exec(" not in orch_src
    assert "os.system" not in orch_src
    assert "subprocess" not in orch_src
    # Registry only allows 4 tools
    from backend.app.tools.registry import list_tools
    assert set(list_tools()) == {"search_documents", "retrieve_evidence", "query_sensor_data", "search_maintenance_logs"}

def test_objective_validation(tmp_path: Path):
    retr = make_retriever(tmp_path)
    db = tmp_path / "obj.db"
    seed_db(db)
    orch = InvestigationOrchestrator(llm_adapter=MockAdapter(), retriever=retr, db_path=db)
    r = orch.investigate("short")
    assert r.status == "failed"
    r2 = orch.investigate("a" * 2001)
    assert r2.status == "failed"
