# AGENT_SPEC.md — Agent & Tool Architecture — SIH26117

> **Status:** Phase 1 — Documentation only (no runtime code yet)
> **Principle:** One orchestrator + deterministic tools. LLM decides WHAT to do; code decides HOW.

## 1. Design Philosophy

- **Single orchestrator, not a swarm.** A swarm of agents is unnecessary overhead on 16 GB / no-GPU and harder to audit. One `InvestigationOrchestrator` plans and sequences tool calls; tools are deterministic functions.
- **LLM is the planner/reasoner; tools are the executors.** The LLM never directly reads files, writes DB rows, or fabricates tool outputs. It outputs a structured intent (tool name + validated args); the tool registry validates and executes.
- **Evidence attachment is mandatory.** Every tool output that contributes to a conclusion must carry evidence refs (docId/page/chunkId or row refs) so the Evidence Engine and Audit Logger can trace it.
- **Bounded execution.** Max steps / max tool calls / timeout per investigation to keep RAM and latency predictable on the target laptop.

## 2. Architecture

```
User objective
  ↓
InvestigationOrchestrator  [backend/app/orchestrator/investigation.py]
  ├── Planner (LLM via LLMAdapter.generate — grounded prompt, tool allowlist)
  ├── Tool Registry  [backend/app/tools/registry.py]
  │     ├── search_documents
  │     ├── retrieve_evidence
  │     ├── query_sensor_data
  │     ├── search_maintenance_logs
  │     ├── inspect_image          (stub until Phase 10)
  │     ├── compare_sources
  │     └── create_report
  ├── Evidence Engine  [backend/app/evidence/engine.py]
  ├── Contradiction Engine  [backend/app/evidence/contradiction.py]
  ├── Safety/Policy Layer  [backend/app/safety/policy.py]
  └── Audit Logger  [backend/app/audit/logger.py]
  ↓
Structured Report (answer + evidenceRefs + evidenceState + conflicts + auditId)
```

## 3. Orchestrator

**Planned location:** `backend/app/orchestrator/investigation.py`

### 3.1 Responsibilities
- Parse investigation objective into a plan (ordered steps).
- For each step: select tool, validate args, call tool, collect output + evidence refs.
- Update Evidence Graph incrementally.
- After retrieval: invoke Evidence Engine + Contradiction Engine.
- Prompt LLM for grounded draft answer (only allowed to cite provided evidence).
- Evidence-gate the draft → assign `EvidenceState`.
- Run safety/policy checks → add disclaimers or block.
- Persist audit trail and return final response.

### 3.2 Planning Prompt Contract
- System prompt constrains LLM to: tool allowlist, JSON-only tool intents, no free-form file I/O, cite evidence.
- Plan schema (Pydantic):
  ```python
  class PlanStep(BaseModel):
      step_no: int
      tool: Literal["search_documents","retrieve_evidence","query_sensor_data",
                     "search_maintenance_logs","inspect_image","compare_sources","create_report"]
      input: dict  # validated per-tool Input model
      rationale: str  # why this step is needed

  class InvestigationPlan(BaseModel):
      objective: str
      steps: list[PlanStep]
  ```
- Plan is validated before execution; unknown tools or malformed args are rejected (logged as `PLAN_VALIDATION_ERROR`).

### 3.3 Execution Bounds
- `MAX_STEPS = 8`, `MAX_TOOL_CALLS = 12`, `STEP_TIMEOUT_S = 30`, `INVESTIGATION_TIMEOUT_S = 180` (tunable via config).
- On bound exceeded: return partial result with `PARTIALLY_SUPPORTED` / `INSUFFICIENT_EVIDENCE` + audit entry `BOUNDS_EXCEEDED`.

## 4. Tool Layer — Contracts

**Planned location:** `backend/app/tools/`

### 4.1 General Tool Contract

Each tool:

```python
class ToolInput(BaseModel):
    # Pydantic — strict, validated
    ...

class ToolOutput(BaseModel):
    result: Any              # tool-specific payload
    evidence_refs: list[EvidenceRef]  # at least one if output bears on a claim
    truncated: bool = False  # if output was truncated for prompt budget
    execution_ms: int

class EvidenceRef(BaseModel):
    doc_id: str | None
    chunk_id: str | None
    page: int | None
    source_file: str
    line_range: tuple[int,int] | None
    sha256: str | None       # source integrity
```

- **Validation:** Pydantic + additional checks (path traversal guard, file-type allowlist, value ranges).
- **Permissions:** tools are read-only except `create_report` (writes `investigations`/`audit_log`); no tool may access network or execute arbitrary code.
- **Error handling:** tools never throw unhandled exceptions to the orchestrator. They return `ToolOutput` with `error: str | None` and the orchestrator logs + decides (retry, skip, or fail investigation).
- **Audit logging:** every tool call logs `{tool, input_hash, output_hash, evidence_refs, timestamp, duration_ms}` via `backend/app/audit/logger.py`.
- **No LLM inside tools.** Tools are deterministic. Only the orchestrator calls the LLM.

### 4.2 Tool Catalog (Planned — Phase 6+)

#### `search_documents`
- **Purpose:** Keyword/hybrid search over ingested docs (vector + metadata filter fallback).
- **Input:**
  ```python
  class SearchDocumentsInput(BaseModel):
      query: str  # 1–500 chars
      top_k: int = 6  # 1–20
      filters: dict | None = None  # e.g., {"equipment_id": "P-204", "doc_type": "SOP"}
  ```
- **Output:** `list[RetrievedChunk]` with `text`, `doc_id`, `chunk_id`, `page`, `score`, `source_file`.
- **Validation:** query length, top_k bounds, filter keys allowlist.
- **Evidence:** each chunk is an evidence ref.

#### `retrieve_evidence`
- **Purpose:** Fetch a specific chunk/doc by ID (for graph/audit drill-down).
- **Input:**
  ```python
  class RetrieveEvidenceInput(BaseModel):
      chunk_id: str | None = None
      doc_id: str | None = None
      # at least one required
  ```
- **Output:** `ChunkDetail` (full text + metadata + sha256).
- **Validation:** ID format, existence check.

#### `query_sensor_data`
- **Purpose:** Deterministic query over structured sensor store (SQLite `sensor_events`).
- **Input:**
  ```python
  class QuerySensorDataInput(BaseModel):
      equipment_id: str  # e.g., "P-204"
      metric: str | None = None  # e.g., "pressure_bar"
      start_time: datetime | None = None
      end_time: datetime | None = None
      aggregation: Literal["raw","avg","max","min"] = "raw"
  ```
- **Output:** `list[SensorRow]` with `timestamp`, `metric`, `value`, `source_file`, `row_id`.
- **Validation:** equipment_id allowlist/pattern, time range sanity, no SQL string interpolation (parameterized queries only).
- **Evidence:** each row carries `source_file` + `row_id` as ref.

#### `search_maintenance_logs`
- **Purpose:** Query maintenance/inspection logs (SQLite `maintenance_logs`).
- **Input:**
  ```python
  class SearchMaintenanceLogsInput(BaseModel):
      equipment_id: str
      start_date: date | None = None
      end_date: date | None = None
      keyword: str | None = None  # e.g., "valve"
  ```
- **Output:** `list[MaintenanceRow]` with `date`, `action`, `source_ref`, `doc_id`.
- **Validation:** date range, keyword length.
- **Evidence:** each row is an evidence ref.

#### `inspect_image` (Stub until Phase 10)
- **Purpose:** Describe/extract info from equipment photo, scanned doc, P&ID diagram via `VisionAdapter`.
- **Input:**
  ```python
  class InspectImageInput(BaseModel):
      image_path: str  # must be under data/raw/ or data/processed/, validated
      prompt: str = "Describe equipment condition and visible anomalies."
  ```
- **Output (Phase 1–9):** `{"status": "NOT_IMPLEMENTED", "message": "Vision support planned for Phase 10."}` + audit entry.
- **Output (Phase 10):** `ImageInsight { description: str, evidence_refs: [...] }`
- **Validation:** path traversal guard, file-type allowlist (`png/jpeg/webp/pdf`), size limit.
- **Permissions:** read-only.

#### `compare_sources`
- **Purpose:** Deterministic + LLM-assisted comparison of two or more evidence items for contradictions.
- **Input:**
  ```python
  class CompareSourcesInput(BaseModel):
      refs: list[EvidenceRef]  # 2–5
      focus: str | None = None  # e.g., "valve replacement status"
  ```
- **Output:** `ComparisonResult { conflicts: list[Conflict], summary: str, evidenceState: EvidenceState }`
  ```python
  class Conflict(BaseModel):
      field: str
      values: list[str]  # e.g., ["Valve replaced on 12 August", "Valve replacement pending"]
      sources: list[EvidenceRef]
      severity: Literal["low","medium","high"]
  ```
- **Behavior:** delegates to `backend/app/evidence/contradiction.py`; may call LLM via adapter for natural-language conflict phrasing (grounded).

#### `create_report`
- **Purpose:** Assemble final structured investigation report from collected evidence/steps.
- **Input:**
  ```python
  class CreateReportInput(BaseModel):
      investigation_id: str
      include_graph: bool = True
      include_audit_summary: bool = True
  ```
- **Output:** `Report { summary, timeline, findings: list[Finding], conflicts, evidenceState, disclaimer, evidenceRefs, auditId }`
  ```python
  class Finding(BaseModel):
      claim: str
      evidenceState: EvidenceState  # SUPPORTED | PARTIALLY_SUPPORTED | INSUFFICIENT_EVIDENCE | CONFLICTING_EVIDENCE
      evidenceRefs: list[EvidenceRef]
      conflicts: list[Conflict] | None
  ```
- **Validation:** investigation must exist and be in a completable state.
- **Permissions:** writes `investigations` status + audit entry.

### 4.3 Registry

**Planned location:** `backend/app/tools/registry.py`

```python
TOOL_REGISTRY: dict[str, ToolSpec] = {
    "search_documents": ToolSpec(input_model=SearchDocumentsInput, handler=search_documents, ...),
    ...
}
def get_tool(name: str) -> ToolSpec: ...
def list_tools() -> list[str]: ...
```

- Single source of truth for allowlist. Orchestrator may only call tools in this registry.
- Registry is imported by both orchestrator and API (for `/api/tools` discovery if needed).

## 5. Input/Output Validation & Error Handling

| Layer | Check | On Failure |
|-------|-------|------------|
| API (Pydantic) | Request schema, size limits | 422, logged |
| Orchestrator plan validation | Tool allowlist, arg schema | `PLAN_VALIDATION_ERROR`, investigation continues or aborts per severity |
| Tool input validation | Field ranges, path guards, ID existence | Tool returns `error` payload, orchestrator logs `TOOL_VALIDATION_ERROR` |
| Tool execution | Exceptions, timeouts | Tool returns `error`, orchestrator logs `TOOL_EXECUTION_ERROR`, may retry once if idempotent |
| Evidence Engine | Citation coverage, grounding | Downgrades `EvidenceState`, adds `MISSING_EVIDENCE` annotation |
| Safety layer | SOP thresholds, safety-critical advice | Adds disclaimer or blocks; logs `POLICY_FLAG` |

All errors are structured, logged to audit, and surfaced in the investigation timeline — never silent.

## 6. Permissions

- Tools are **read-only** by default. Only `create_report` (and audit logger) write to `investigations`/`audit_log`.
- No tool may: access network, execute shell commands, write arbitrary files, or escalate beyond `data/` + `data/vector_store/` + SQLite DB.
- Path validation: all file paths resolved and checked to be under `data/raw/` or `data/processed/`; reject `..`, absolute paths, symlinks outside allowed roots.
- Future: if multi-user is ever added, add `user_id` scoping at the API + tool layers (not required for single-workstation MVP).

## 7. Evidence Attachment

- Every tool output that can bear on a conclusion must include `evidence_refs`. The orchestrator rejects tool outputs that claim to support a finding but carry no refs (logged as `MISSING_EVIDENCE_REF`).
- Evidence refs are propagated into the Evidence Graph (nodes/edges) and into the final report's `evidenceRefs` per finding.
- Evidence refs are content-addressed via `sha256` of source file/chunk so tampering is detectable (see `docs/SECURITY.md`).

## 8. Audit Logging

- Every orchestrator decision and tool call is logged via `backend/app/audit/logger.py` (see `docs/ARCHITECTURE.md:3.14`).
- Log entry schema:
  ```python
  class AuditEntry(BaseModel):
      id: str
      investigation_id: str
      timestamp: datetime
      actor: Literal["user","orchestrator","tool","evidence_engine","safety_layer"]
      action: str  # e.g., "TOOL_CALL", "EVIDENCE_STATE_ASSIGNED", "CONFLICT_FLAGGED"
      payload: dict  # truncated + hashed for large payloads
      payload_hash: str
  ```
- Logs are append-only; no deletion API.
- Replay: `GET /api/audit?investigationId=` returns ordered entries sufficient to reconstruct the investigation.

## 9. Testing Strategy

- **Unit:** each tool's input validation, path guards, Pydantic schemas (`tests/test_tools_*.py`).
- **Unit:** orchestrator plan validation, bounds enforcement, error paths.
- **Integration:** orchestrator → tool → evidence engine → report with `MockAdapter` (no model, no network).
- **Fixtures:** synthetic flagship data (SOP, sensor CSV, maintenance logs) under `tests/fixtures/` — never real confidential data.
- **No test may require a running model or network.** MockAdapter provides deterministic LLM outputs.

## 10. Non-Goals

- No autonomous code execution, no shell tools, no web browsing tools, no cloud API tools in default path.
- No swarm/multi-agent coordination (single orchestrator is sufficient and auditable).
- No tool may bypass evidence gating or audit logging.

---

**Last updated:** 2026-08-27 — Phase 1. Tool contracts are planned interfaces; implementation begins Phase 6.
