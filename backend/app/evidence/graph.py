"""EvidenceGraph — deterministic, offline, no LLM, no network."""

import re
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class NodeType(str, Enum):
    INVESTIGATION = "investigation"
    STEP = "step"
    TOOL = "tool"
    DOCUMENT = "document"
    CHUNK = "chunk"
    CLAIM = "claim"


class EdgeType(str, Enum):
    CONTAINS = "CONTAINS"
    USED_TOOL = "USED_TOOL"
    PRODUCED = "PRODUCED"
    SUPPORTED_BY = "SUPPORTED_BY"
    DERIVED_FROM = "DERIVED_FROM"


class Node(BaseModel):
    id: str
    type: NodeType
    label: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Edge(BaseModel):
    id: str
    from_node: str
    to_node: str
    relation: EdgeType
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceGraph(BaseModel):
    nodes: dict[str, Node] = Field(default_factory=dict)
    edges: dict[str, Edge] = Field(default_factory=dict)  # key = edge id

    # --- API ---
    def add_node(self, node_type: NodeType | str, node_id: str, label: str, metadata: dict[str, Any] | None = None) -> Node:
        if not node_id or not isinstance(node_id, str):
            raise ValueError("node_id must be non-empty string")
        if node_id in self.nodes:
            return self.nodes[node_id]
        nt = NodeType(node_type) if isinstance(node_type, str) else node_type
        node = Node(id=node_id, type=nt, label=label, metadata=metadata or {})
        self.nodes[node_id] = node
        return node

    def add_edge(self, from_node: str, to_node: str, relation: EdgeType | str, metadata: dict[str, Any] | None = None) -> Edge:
        if from_node not in self.nodes:
            raise ValueError(f"from_node not found: {from_node}")
        if to_node not in self.nodes:
            raise ValueError(f"to_node not found: {to_node}")
        rel = EdgeType(relation) if isinstance(relation, str) else relation
        edge_id = f"{from_node}--{rel.value}--{to_node}"
        if edge_id in self.edges:
            return self.edges[edge_id]
        edge = Edge(id=edge_id, from_node=from_node, to_node=to_node, relation=rel, metadata=metadata or {})
        self.edges[edge_id] = edge
        return edge

    def get_node(self, node_id: str) -> Node | None:
        return self.nodes.get(node_id)

    def get_neighbors(self, node_id: str, direction: str = "out") -> list[Node]:
        if node_id not in self.nodes:
            raise ValueError(f"node not found: {node_id}")
        if direction not in ("out", "in", "both"):
            raise ValueError("direction must be out|in|both")
        neigh_ids: set[str] = set()
        for e in self.edges.values():
            if direction in ("out", "both") and e.from_node == node_id:
                neigh_ids.add(e.to_node)
            if direction in ("in", "both") and e.to_node == node_id:
                neigh_ids.add(e.from_node)
        return [self.nodes[nid] for nid in neigh_ids if nid in self.nodes]

    def get_subgraph(self, node_ids: list[str]) -> "EvidenceGraph":
        keep = set(node_ids)
        sub = EvidenceGraph()
        for nid in keep:
            if nid in self.nodes:
                n = self.nodes[nid]
                sub.add_node(n.type, n.id, n.label, n.metadata)
        for e in self.edges.values():
            if e.from_node in keep and e.to_node in keep:
                # ensure nodes exist
                if e.from_node in sub.nodes and e.to_node in sub.nodes:
                    sub.add_edge(e.from_node, e.to_node, e.relation, e.metadata)
        return sub

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.model_dump() for n in self.nodes.values()],
            "edges": [e.model_dump() for e in self.edges.values()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceGraph":
        g = cls()
        for n in data.get("nodes", []):
            g.add_node(n["type"], n["id"], n["label"], n.get("metadata", {}))
        for e in data.get("edges", []):
            # nodes already added, edges will validate
            try:
                g.add_edge(e["from_node"], e["to_node"], e["relation"], e.get("metadata", {}))
            except ValueError:
                # skip invalid edge (should not happen for valid dict)
                continue
        return g

    @classmethod
    def from_investigation(cls, report) -> "EvidenceGraph":
        """Build graph: Investigation -> Step -> Tool -> Evidence -> Document -> Chunk + Claim SUPPORTED_BY Chunk"""
        g = cls()
        inv_id = report.investigation_id
        # Investigation node
        g.add_node(NodeType.INVESTIGATION, inv_id, report.objective[:80] if report.objective else inv_id, {"objective": report.objective, "status": report.status, "evidence_state": report.evidence_state.value if report.evidence_state else None})

        # Claim nodes from summary (split into claims)
        from backend.app.evidence.engine import split_claims

        claims = split_claims(report.summary) if report.summary else []
        claim_ids: list[str] = []
        for idx, claim in enumerate(claims):
            cid = f"{inv_id}:claim:{idx}"
            g.add_node(NodeType.CLAIM, cid, claim[:80], {"text": claim, "index": idx})
            claim_ids.append(cid)
            # Investigation CONTAINS Claim
            g.add_edge(inv_id, cid, EdgeType.CONTAINS)

        # Tool nodes dedup
        tool_nodes: dict[str, str] = {}  # tool name -> node id
        # Document/chunk dedup maps
        doc_nodes: dict[str, str] = {}  # doc key -> node id
        chunk_nodes: dict[str, str] = {}

        for step in report.steps_executed:
            step_id = f"{inv_id}:step:{step.step_no}"
            g.add_node(NodeType.STEP, step_id, f"Step {step.step_no}: {step.tool}", {"tool": step.tool, "success": step.success, "rationale": step.rationale})
            g.add_edge(inv_id, step_id, EdgeType.CONTAINS)

            # Tool node
            tool_name = step.tool
            if tool_name not in tool_nodes:
                tid = f"tool:{tool_name}"
                g.add_node(NodeType.TOOL, tid, tool_name, {})
                tool_nodes[tool_name] = tid
            tid = tool_nodes[tool_name]
            g.add_edge(step_id, tid, EdgeType.USED_TOOL)

            # Evidence refs per step
            for ref in step.evidence_refs:
                # Document node key: document_id or filename+sha256
                doc_key = ref.document_id or f"{ref.filename}:{ref.sha256 or ''}"
                if doc_key not in doc_nodes:
                    doc_id = f"doc:{doc_key}"
                    # sanitize doc_id
                    doc_id = re.sub(r"[^A-Za-z0-9:_\-\.]", "_", doc_id)[:120]
                    g.add_node(NodeType.DOCUMENT, doc_id, ref.filename or doc_key[:40], {"document_id": ref.document_id, "filename": ref.filename, "sha256": ref.sha256, "source_path": ref.source_path})
                    doc_nodes[doc_key] = doc_id
                doc_id = doc_nodes[doc_key]

                # Chunk node if chunk_id present
                chunk_id = ref.chunk_id
                if chunk_id:
                    if chunk_id not in chunk_nodes:
                        # preserve provenance
                        g.add_node(NodeType.CHUNK, chunk_id, chunk_id, {"chunk_id": ref.chunk_id, "document_id": ref.document_id, "filename": ref.filename, "page_number": ref.page_number, "line_range": getattr(ref, "line_range", None), "sha256": ref.sha256, "source_path": ref.source_path, "score": ref.score, "text_snippet": getattr(ref, "text_snippet", None)})
                        chunk_nodes[chunk_id] = chunk_id
                    # Step PRODUCED Chunk
                    g.add_edge(step_id, chunk_id, EdgeType.PRODUCED)
                    # Tool PRODUCED Chunk as well
                    try:
                        g.add_edge(tid, chunk_id, EdgeType.PRODUCED)
                    except ValueError:
                        pass
                    # Chunk DERIVED_FROM Document
                    try:
                        g.add_edge(chunk_id, doc_id, EdgeType.DERIVED_FROM)
                    except ValueError:
                        pass
                    # Document CONTAINS Chunk
                    try:
                        g.add_edge(doc_id, chunk_id, EdgeType.CONTAINS)
                    except ValueError:
                        pass
                    # Claim SUPPORTED_BY Chunk
                    for cid in claim_ids:
                        # Only link if claim text mentions filename or evidence is relevant? For Phase 8, link all claims to all chunks from successful steps
                        # But to keep deterministic and not over-connect, link each claim to each chunk produced by successful steps
                        if step.success:
                            try:
                                g.add_edge(cid, chunk_id, EdgeType.SUPPORTED_BY)
                            except ValueError:
                                pass
                else:
                    # No chunk_id: step PRODUCED Document directly
                    g.add_edge(step_id, doc_id, EdgeType.PRODUCED)
                    for cid in claim_ids:
                        if step.success:
                            try:
                                g.add_edge(cid, doc_id, EdgeType.SUPPORTED_BY)
                            except ValueError:
                                pass

        # Also handle top-level evidence_refs that may not be tied to step (from report)
        # Already covered via steps; no extra handling needed

        return g

    # Persistence helpers (optional)
    def save_to_db(self, investigation_id: str, db_path=None) -> None:
        from backend.app.store.db import get_connection
        conn = get_connection(db_path)
        try:
            # Ensure tables
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS evidence_graph_nodes (
                id TEXT PRIMARY KEY,
                investigation_id TEXT NOT NULL,
                type TEXT NOT NULL,
                label TEXT NOT NULL,
                metadata TEXT
            );
            CREATE TABLE IF NOT EXISTS evidence_graph_edges (
                id TEXT PRIMARY KEY,
                investigation_id TEXT NOT NULL,
                from_node TEXT NOT NULL,
                to_node TEXT NOT NULL,
                relation TEXT NOT NULL
            );
            """)
            import json as _json
            # Clear existing for this investigation (idempotent)
            conn.execute("DELETE FROM evidence_graph_nodes WHERE investigation_id=?", (investigation_id,))
            conn.execute("DELETE FROM evidence_graph_edges WHERE investigation_id=?", (investigation_id,))
            for n in self.nodes.values():
                conn.execute(
                    "INSERT OR REPLACE INTO evidence_graph_nodes (id, investigation_id, type, label, metadata) VALUES (?, ?, ?, ?, ?)",
                    (n.id, investigation_id, n.type.value, n.label, _json.dumps(n.metadata)),
                )
            for e in self.edges.values():
                conn.execute(
                    "INSERT OR REPLACE INTO evidence_graph_edges (id, investigation_id, from_node, to_node, relation) VALUES (?, ?, ?, ?, ?)",
                    (e.id, investigation_id, e.from_node, e.to_node, e.relation.value),
                )
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def load_from_db(cls, investigation_id: str, db_path=None) -> "EvidenceGraph":
        from backend.app.store.db import get_connection
        import json as _json
        g = cls()
        conn = get_connection(db_path)
        try:
            # Ensure tables exist
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS evidence_graph_nodes (
                id TEXT PRIMARY KEY,
                investigation_id TEXT NOT NULL,
                type TEXT NOT NULL,
                label TEXT NOT NULL,
                metadata TEXT
            );
            CREATE TABLE IF NOT EXISTS evidence_graph_edges (
                id TEXT PRIMARY KEY,
                investigation_id TEXT NOT NULL,
                from_node TEXT NOT NULL,
                to_node TEXT NOT NULL,
                relation TEXT NOT NULL
            );
            """)
            cur = conn.execute("SELECT id, type, label, metadata FROM evidence_graph_nodes WHERE investigation_id=?", (investigation_id,))
            for row in cur.fetchall():
                meta = _json.loads(row["metadata"]) if row["metadata"] else {}
                g.add_node(row["type"], row["id"], row["label"], meta)
            cur2 = conn.execute("SELECT id, from_node, to_node, relation FROM evidence_graph_edges WHERE investigation_id=?", (investigation_id,))
            for row in cur2.fetchall():
                try:
                    g.add_edge(row["from_node"], row["to_node"], row["relation"])
                except ValueError:
                    continue
        finally:
            conn.close()
        return g
