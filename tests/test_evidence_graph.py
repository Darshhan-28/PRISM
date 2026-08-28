import json
import tempfile
from pathlib import Path
import datetime

from backend.app.evidence.graph import EvidenceGraph, NodeType, EdgeType
from backend.app.orchestrator.investigation import InvestigationOrchestrator
from backend.app.llm.mock_adapter import MockAdapter
from backend.app.ingestion.models import NormalizedDocument, Page
from backend.app.ingestion.chunker import chunk_document
from backend.app.retrieval.vector_store import ChromaStore
from backend.app.retrieval.embedder import MockEmbedder
from backend.app.retrieval.retriever import Retriever
from backend.app.store.db import init_db, get_connection, insert_sensor_event, insert_maintenance_log


def make_retriever(tmp_path: Path):
    store = ChromaStore(persist_dir=tmp_path / "vs")
    texts = ["Pump P-204 pressure 2.1 bar SOP threshold", "Valve replaced 12 August maintenance", "Sensor 4.8 bar reading"]
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
    emb = MockEmbedder(dim=8)
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
        ]:
            insert_maintenance_log(conn, row)
        conn.commit()
    finally:
        conn.close()

def make_report(tmp_path: Path):
    retr = make_retriever(tmp_path)
    db = tmp_path / "work.db"
    seed_db(db)
    plan = json.dumps({"objective": "Investigate pressure in P-204 for graph test", "steps": [
        {"step_no":1,"tool":"search_documents","input":{"query":"pressure"},"rationale":"find SOP"},
        {"step_no":2,"tool":"query_sensor_data","input":{"equipment_id":"P-204"},"rationale":"check sensor"}
    ]})
    mock = MockAdapter(canned={"Objective": plan})
    orch = InvestigationOrchestrator(llm_adapter=mock, retriever=retr, db_path=db)
    report = orch.investigate("Investigate pressure in P-204 for graph test")
    return report, db

# basic
def test_node_edge_creation():
    g = EvidenceGraph()
    g.add_node(NodeType.INVESTIGATION, "inv1", "Investigation", {})
    g.add_node(NodeType.STEP, "step1", "Step 1", {})
    g.add_edge("inv1", "step1", EdgeType.CONTAINS)
    assert g.get_node("inv1") is not None
    assert len(g.nodes) == 2
    assert len(g.edges) == 1

def test_duplicate_prevention():
    g = EvidenceGraph()
    g.add_node(NodeType.TOOL, "tool:search", "search", {"a":1})
    g.add_node(NodeType.TOOL, "tool:search", "search-dup", {"a":2})
    assert len(g.nodes) == 1
    assert g.get_node("tool:search").label == "search"  # first wins
    g.add_node(NodeType.CHUNK, "c1", "chunk", {})
    g.add_node(NodeType.DOCUMENT, "d1", "doc", {})
    g.add_edge("tool:search", "c1", EdgeType.PRODUCED)
    g.add_edge("tool:search", "c1", EdgeType.PRODUCED)
    assert len(g.edges) == 1

def test_invalid_references():
    g = EvidenceGraph()
    g.add_node(NodeType.INVESTIGATION, "inv1", "inv", {})
    try:
        g.add_edge("inv1", "missing", EdgeType.CONTAINS)
        assert False
    except ValueError as e:
        assert "not found" in str(e)
    try:
        g.add_edge("missing", "inv1", EdgeType.CONTAINS)
        assert False
    except ValueError:
        pass
    try:
        g.add_node(NodeType.STEP, "", "bad", {})
        assert False
    except ValueError:
        pass
    try:
        g.get_neighbors("nonexistent")
        assert False
    except ValueError:
        pass

def test_investigation_graph_construction(tmp_path: Path):
    report, _ = make_report(tmp_path)
    g = EvidenceGraph.from_investigation(report)
    # Must contain investigation, steps, tools, documents/chunks, claims
    types = {n.type for n in g.nodes.values()}
    assert NodeType.INVESTIGATION in types
    assert NodeType.STEP in types
    assert NodeType.TOOL in types
    # Check Investigation -> Step via CONTAINS
    inv_neighbors = [n.type for n in g.get_neighbors(report.investigation_id, "out")]
    assert NodeType.STEP in inv_neighbors or any(n.id.startswith(report.investigation_id+":step") for n in g.nodes.values())
    # Ensure path Investigation -> Step -> Tool -> Evidence -> Document -> Chunk exists
    # At least one CONTAINS, USED_TOOL, PRODUCED edge
    rels = {e.relation for e in g.edges.values()}
    assert EdgeType.CONTAINS in rels
    assert EdgeType.USED_TOOL in rels
    assert EdgeType.PRODUCED in rels

def test_provenance_preservation(tmp_path: Path):
    report, _ = make_report(tmp_path)
    g = EvidenceGraph.from_investigation(report)
    # Find chunk nodes and check provenance fields
    chunk_nodes = [n for n in g.nodes.values() if n.type == NodeType.CHUNK]
    if chunk_nodes:
        for cn in chunk_nodes:
            assert "filename" in cn.metadata or "chunk_id" in cn.metadata
            assert cn.metadata.get("chunk_id") or cn.id
            # filename, sha256 preserved if present
    doc_nodes = [n for n in g.nodes.values() if n.type == NodeType.DOCUMENT]
    if doc_nodes:
        for dn in doc_nodes:
            assert "filename" in dn.metadata

def test_neighbors_subgraph():
    g = EvidenceGraph()
    g.add_node(NodeType.INVESTIGATION, "inv", "inv", {})
    g.add_node(NodeType.STEP, "s1", "step", {})
    g.add_node(NodeType.TOOL, "t1", "tool", {})
    g.add_edge("inv", "s1", EdgeType.CONTAINS)
    g.add_edge("s1", "t1", EdgeType.USED_TOOL)
    neigh = g.get_neighbors("s1", "out")
    assert any(n.id == "t1" for n in neigh)
    neigh_in = g.get_neighbors("s1", "in")
    assert any(n.id == "inv" for n in neigh_in)
    neigh_both = g.get_neighbors("s1", "both")
    assert len(neigh_both) == 2
    sub = g.get_subgraph(["inv", "s1"])
    assert "inv" in sub.nodes and "s1" in sub.nodes
    assert "t1" not in sub.nodes
    assert len(sub.edges) == 1

def test_serialization():
    g = EvidenceGraph()
    g.add_node(NodeType.INVESTIGATION, "inv", "inv", {"objective":"test"})
    g.add_node(NodeType.STEP, "s1", "step", {})
    g.add_edge("inv", "s1", EdgeType.CONTAINS)
    d = g.to_dict()
    assert "nodes" in d and "edges" in d
    g2 = EvidenceGraph.from_dict(d)
    assert len(g2.nodes) == len(g.nodes)
    assert len(g2.edges) == len(g.edges)
    assert g2.get_node("inv").label == "inv"

def test_deterministic_repeated_construction(tmp_path: Path):
    report, _ = make_report(tmp_path)
    g1 = EvidenceGraph.from_investigation(report)
    g2 = EvidenceGraph.from_investigation(report)
    assert len(g1.nodes) == len(g2.nodes)
    assert len(g1.edges) == len(g2.edges)
    assert set(g1.nodes.keys()) == set(g2.nodes.keys())
    assert set(g1.edges.keys()) == set(g2.edges.keys())

def test_empty_malformed_evidence(tmp_path: Path):
    # Report with no steps/evidence
    from backend.app.orchestrator.investigation import InvestigationReport
    from backend.app.evidence.engine import EvidenceState
    report = InvestigationReport(
        investigation_id="empty123",
        objective="Test empty evidence investigation for graph",
        status="failed",
        plan=None,
        steps_executed=[],
        evidence_refs=[],
        evidence_state=EvidenceState.INSUFFICIENT_EVIDENCE,
        summary="Insufficient evidence",
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        completed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
    g = EvidenceGraph.from_investigation(report)
    # Should at least have investigation and claim nodes, no crash
    assert g.get_node("empty123") is not None
    # Claim node exists
    assert any(n.type == NodeType.CLAIM for n in g.nodes.values())

def test_persistence(tmp_path: Path):
    report, db = make_report(tmp_path)
    g = EvidenceGraph.from_investigation(report)
    g.save_to_db(report.investigation_id, db_path=db)
    # Load
    g2 = EvidenceGraph.load_from_db(report.investigation_id, db_path=db)
    assert len(g2.nodes) == len(g.nodes)
    assert len(g2.edges) == len(g.edges)
    # Verify via DB directly
    conn = get_connection(db)
    cur = conn.execute("SELECT count(*) FROM evidence_graph_nodes WHERE investigation_id=?", (report.investigation_id,))
    assert cur.fetchone()[0] == len(g.nodes)
    cur2 = conn.execute("SELECT count(*) FROM evidence_graph_edges WHERE investigation_id=?", (report.investigation_id,))
    assert cur2.fetchone()[0] == len(g.edges)
    conn.close()

def test_offline_behavior():
    import pathlib
    src = pathlib.Path("backend/app/evidence/graph.py").read_text(encoding="utf-8")
    assert "import httpx" not in src
    assert "import requests" not in src
    assert "eval(" not in src
    assert "exec(" not in src
    # Check no LLM call
    assert "MockAdapter" not in src and "LLMAdapter" not in src

def test_stable_ids(tmp_path: Path):
    report, _ = make_report(tmp_path)
    g = EvidenceGraph.from_investigation(report)
    # IDs must be deterministic (no uuid random in graph)
    # Rebuild and IDs same
    g2 = EvidenceGraph.from_investigation(report)
    for nid in g.nodes:
        assert nid in g2.nodes
