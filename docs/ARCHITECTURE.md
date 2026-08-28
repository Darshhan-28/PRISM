# ARCHITECTURE.md — SIH26117 Sovereign On-Premise Agentic AI Workbench

> **Status:** Phase 11 — Safety + Auditability (Complete)
> **Hardware target:** Windows 11, Intel 12th-gen mobile, 16 GB RAM, Intel Iris Xe (no NVIDIA GPU), 512 GB NVMe
> **Principle:** Local-first, model-agnostic, lightweight, auditable.

## 1. Overview

The workbench is a single-machine, fully on-premise investigative system. All core paths — document ingestion, retrieval, LLM inference, tool execution, evidence validation, audit — run offline after initial setup. Network access is never required at query time.

The LLM is an **orchestrator/reasoner**, not an authority. Deterministic application code owns retrieval, tool execution, evidence gating, contradiction detection, and audit.

## 2. High-Level Data Flow

```
User
 ↓
Workbench UI (frontend/ — React/Vite, lightweight)
 ↓  HTTP/JSON (FastAPI)
Backend API  [backend/app/api/]
 ↓
Investigation Orchestrator  [backend/app/orchestrator/]
 ├── Retriever  ─────────────→ Vector Store (local) + Structured Store
 ├── Tool Layer  ────────────→ Deterministic local tools (no network)
 ├── Evidence Engine  ───────→ Evidence states + grounding checks
 ├── Contradiction Engine  ──→ Conflict detection across sources
 ├── Safety/Policy Layer  ───→ SOP & safety-critical gating
 └── LLM Adapter  ───────────→ Local model runtime (Ollama / llama.cpp / Mock)
 ↓
Validated Response (answer + evidenceRefs + evidenceState + auditId)
 ↓
Evidence Graph + Audit Trail  →  persisted locally, replayable
 ↓
Workbench UI (render answer, evidence cards, graph, audit log)
```

Offline invariant: if `internet_disabled == true`, the entire flow above still runs (model, docs, DB, vector store, tools are all local).

## 3. Components

### 3.1 Frontend — Workbench UI

**Planned location:** `frontend/src/`

- Lightweight Vite + TypeScript + React (or equivalent — keep bundle small for 16 GB host).
- Views:
  - **Workbench (Chat + Evidence panel):** query input, streaming answer, evidence cards with doc/page/line refs, evidence-state badge.
  - **Investigation View:** objective, plan steps, tool calls, retrieved evidence, contradiction flags, final structured report, export.
  - **Evidence Graph View:** nodes = Incident / Equipment / Sensor Event / Maintenance Record / SOP / Finding; edges = supports / contradicts / references.
  - **Audit/Replay View:** timestamp, query, retrieved chunks, tools, decisions, final response.
- No direct LLM calls from frontend. All via backend API.
- Offline-friendly: static build served by FastAPI or simple dev server; no CDN dependency in production build.

### 3.2 Backend API

**Planned location:** `backend/app/api/`, `backend/app/main.py`

- FastAPI, single process (Uvicorn), single port.
- Endpoints (planned, not yet implemented):
  - `POST /api/ingest` — upload doc (PDF/CSV/JSON/image), validated, queued for parsing.
  - `POST /api/query` — simple evidence-backed Q&A.
  - `POST /api/investigations` — start investigation (objective + scope).
  - `GET  /api/investigations/{id}` — status, steps, evidence, report.
  - `GET  /api/evidence/{id}` — chunk + source metadata.
  - `GET  /api/audit?investigationId=` — replay trail.
  - `GET  /api/health` — model/vector-store/DB readiness.
- Pydantic models for all request/response schemas.
- Structured JSON logging.

### 3.3 Local Model Runtime + Model Adapter

**Location:** `backend/app/llm/` (Phase 4 complete)

**Adapter interface:** `backend/app/llm/adapter.py:1`
```python
class LLMAdapter(Protocol):
    def generate(self, prompt: str, system: str | None, **kwargs) -> str: ...
    async def generate_stream(self, prompt: str, system: str | None, **kwargs) -> AsyncIterator[str]: ...
    def health_check(self) -> dict: ...
class EmbeddingAdapter(Protocol):  # in backend/app/retrieval/embedder.py:10
    def embed(self, texts: list[str]) -> list[list[float]]: ...
class VisionAdapter(Protocol):  # stub until Phase 10
    def describe_image(self, image_bytes: bytes, prompt: str) -> str: ...
```

- **Implementations:**
  - `OllamaAdapter` (`backend/app/llm/ollama_adapter.py:1`) — httpx to `http://localhost:11434`, localhost-only guard (`allow_cloud_adapter=false`), timeout 30s, typed errors `LLMTimeoutError`/`LLMUnavailableError`.
  - `MockAdapter` (`backend/app/llm/mock_adapter.py:1`) — deterministic, canned responses, default for tests/CI (no model, no network).
  - `LlamaCppAdapter` — deferred (raises `NotImplementedError` via factory).
  - Optional `CloudAdapter` — only if `ALLOW_CLOUD_ADAPTER=true` (never default).
- Candidates (quantized Q4_K_M, CPU): Qwen2.5 1.5B/3B, Phi-3 mini 3.8B — not downloaded in Phase 4.
- Configuration via `backend/app/config.py:48` (`llm_provider`, `llm_model`, `llm_host`, `llm_timeout_s`, `allow_cloud_adapter`) — no code change to swap.
- Resource guard: single model at a time, ~1.5 GB for Q4 1.5B + 0.1 GB adapter overhead (see §5).

### 3.4 Document Ingestion

**Planned location:** `backend/app/ingestion/`

- **Inputs:** `data/raw/` — PDF, CSV, JSON, TXT, LOG, MD, images (allowlist enforced; see `docs/SECURITY.md`).
- **Stages:**
  1. **Validation:** file-type check (magic bytes + extension), size limit, path-traversal guard, virus/malicious-content flagging (treat docs as untrusted). Implemented in `backend/app/ingestion/validator.py:1`.
  2. **Parser:** per-type parser → normalized `Document` + `Page` + raw text/structured rows. Implemented in `backend/app/ingestion/parsers/*.py`.
     - PDF: `pypdf` (lightweight, CPU-only) — text extraction per page; OCR deferred to Phase 10.
     - CSV/JSON: stdlib `csv`/`json` → typed rows + text representation.
     - TXT/LOG: line-preserving read with encoding cascade.
     - MD: heading-aware section split (`#` → `Page.section`).
     - Images: stored + stub `VisionAdapter.describe_image` in Phase 10.
  3. **Chunking:** `backend/app/ingestion/chunker.py:1` — deterministic, overlapping chunks (800 chars, 120 overlap), preserve doc/page/line metadata, no LLM in chunking. Sentence-boundary preference in last 20% of window.
  4. **Enrichment:** full provenance metadata (`document_id`, `chunk_id`, `sha256`, `page`, `line_range`, `section`, `ingested_at`).
- **Outputs:** `data/processed/` — `{doc_id}.json` + `{doc_id}.chunks.jsonl` + SQLite `data/workbench.db` + vector store `data/vector_store/`.

### 3.5 Embeddings

**Location:** `backend/app/retrieval/embedder.py`

- Local embedding model via `EmbeddingAdapter` (same adapter pattern). Default `MockEmbedder` for tests; `FastEmbedEmbedder` for prod.
- **Selected (Phase 3 benchmark 2026-08-28, docs/BENCHMARK_EMBEDDINGS.md):** `sentence-transformers/all-MiniLM-L6-v2` (384 dim, ~80 MB ONNX) — 10.3s load, 98 MB delta, 14ms single, 1.0 top-1 vs BGE 13.5s/113MB/69ms/0.6. Runner-up `BAAI/bge-small-en-v1.5` remains swappable via `EMBEDDING_MODEL`.
- Batch embedding at ingest; single-text embedding at query.
- CPU-only, OFFLINE after download (cached `~/.cache/fastembed` or `%LOCALAPPDATA%`).

### 3.6 Vector Store

**Planned location:** `backend/app/retrieval/vector_store.py`, persisted under `data/vector_store/`

- Lightweight local store: **Chroma** (SQLite backend) or **sqlite-vec** — chosen for zero-service footprint (no separate DB process).
- Collections: `chunks` (text + embedding + metadata: docId, page, chunkId, sourceHash).
- Operations: `upsert(chunks)`, `search(queryEmbedding, k, filters)`, `delete(docId)`.
- Single-file persistence so the workbench is portable on the 512 GB NVMe.

### 3.7 Structured Data Store

**Planned location:** `backend/app/store/` — SQLite

- **Files:** `data/workbench.db` (SQLite) — no Postgres/Docker required.
- **Tables (planned):**
  - `documents(id, filename, type, sha256, ingested_at)`
  - `chunks(id, doc_id, page, text, token_count)`
  - `sensor_events(id, equipment_id, timestamp, metric, value, source_file)`
  - `maintenance_logs(id, equipment_id, date, action, source_ref)`
  - `investigations(id, objective, status, created_at, completed_at)`
  - `investigation_steps(id, investigation_id, step_no, tool, input, output, evidence_refs)`
  - `evidence_graph_nodes(id, investigation_id, type, label, source_ref)`
  - `evidence_graph_edges(id, investigation_id, from_node, to_node, relation)`  # relations: supports | contradicts | references
  - `audit_log(id, investigation_id, timestamp, actor, action, payload_hash)`
- All tool outputs that are structured (sensor CSVs, etc.) land here for deterministic querying.

### 3.8 Retrieval (RAG)

**Location:** `backend/app/retrieval/retriever.py:1` (Phase 3 complete)

- Query → `EmbeddingAdapter.embed` → `ChromaStore.search` (top_k 1..20, default 6) → distance→similarity (`score = 1 - distance`) → threshold filter (`retrieval_threshold`, 0.0 = no filter) → allowed metadata filters (`filename`, `file_type`, `document_id`, `sha256`, etc.) → returns `RetrievedChunk[chunk_id, text, score, distance, metadata]`.
- Full provenance preserved (`chunk_id`, `document_id`, `filename`, `page_number`, `sha256`) for evidence gating.
- No LLM in retrieval; deterministic; CPU-only; works offline with `MockEmbedder` or `FastEmbedEmbedder` (MiniLM default).

### 3.9 Tool Layer

**Location:** `backend/app/tools/` (Phase 6 complete)

- Deterministic Python functions via `backend/app/tools/{search_documents.py:1,retrieve_evidence.py:1,query_sensor_data.py:1,search_maintenance_logs.py:1}`, no LLM, no network, no shell. Pydantic `Input`/`ToolOutput{success,result,evidence_refs,error}`.
- Registry `backend/app/tools/registry.py:1` — explicit allowlist `search_documents|retrieve_evidence|query_sensor_data|search_maintenance_logs`, `execute_tool()` validates name+input before execution.
- Tools reuse `Retriever`/`ChromaStore`/`SQLite workbench.db`; preserve `filename,document_id,chunk_id,page_number/line_range,sha256`.
- Planned deferred: `inspect_image` (Phase 10), `compare_sources`, `create_report` (Phase 7+).

### 3.10 Agent / Investigation Orchestrator

**Location:** `backend/app/orchestrator/investigation.py:1` (Phase 7 complete)

- Bounded orchestrator `InvestigationOrchestrator(MAX_STEPS=8, STEP_TIMEOUT=30s, MAX_TOOL_CALLS=8)` — validates objective 10..2000 chars, asks `LLMAdapter` for `InvestigationPlan{objective, steps: InvestigationStep[step_no,tool,input,rationale]}`, extracts JSON, validates sequential step_no, tool allowlist, Pydantic input per tool, rejects unknown/duplicate/excessive/invalid.
- Execution: `registry.execute_tool()` deterministic, per-step timing, evidence_refs collection (handles None chunk_id for sensor), pseudo `RetrievedChunk` for evidence gating, `evidence/engine.evaluate` → `EvidenceState`, summary via grounded prompt `<RETRIEVED_CHUNK>`.
- Persistence: `investigations(id,objective,status,evidence_state,summary,created_at,completed_at)` + `investigation_steps(id,investigation_id,step_no,tool,input,rationale,success,result,error,evidence_refs)` in `data/workbench.db` (existing DB, no new DB).
- No recursion, no shell, no network, no arbitrary Python — LLM plans only, code executes. MockAdapter default.

### 3.11 Evidence Engine

**Location:** `backend/app/evidence/engine.py:1` (Phase 5 complete)

- Inputs: retrieved chunks + LLM answer.
- Checks: keyword conflict (`replaced` vs `pending`, `normal` vs `exceeds`), citation parse `\[...\]`, invalid citation, per-claim coverage (split by sentence), provenance dedup.
- Output: `EvidenceResult{state: SUPPORTED|PARTIALLY_SUPPORTED|INSUFFICIENT_EVIDENCE|CONFLICTING_EVIDENCE, evidence_refs: EvidenceRef[], citations_found, invalid_citations, missing_coverage, conflicting}`.

### 3.11b Evidence Graph

**Location:** `backend/app/evidence/graph.py:1` (Phase 8 complete)

- Deterministic Pydantic `EvidenceGraph{nodes: dict, edges: dict}` with 6 node types `investigation|step|tool|document|chunk|claim` and 5 edge types `CONTAINS|USED_TOOL|PRODUCED|SUPPORTED_BY|DERIVED_FROM`.
- API: `add_node`, `add_edge` (duplicate check, invalid ref rejection), `get_node`, `get_neighbors`, `get_subgraph`, `to_dict`/`from_dict`, `from_investigation(report)` builds `Investigation->Step->Tool->Evidence->Document->Chunk` plus `Claim SUPPORTED_BY Chunk`.
- Reuses `InvestigationReport`/`EvidenceRef` (no duplication), preserves `document_id,chunk_id,filename,page_number,line_range,sha256,source_path,score`, stable IDs, idempotent construction.
- Persistence: `evidence_graph_nodes(id,investigation_id,type,label,metadata)` + `evidence_graph_edges(id,investigation_id,from_node,to_node,relation)` in `data/workbench.db` via `save_to_db`/`load_from_db` (no new DB, offline, no LLM).
- Integration: `answer_with_evidence(query, retriever, llm_adapter)` → `Retriever.retrieve` → `build_grounded_prompt` (delimited `<RETRIEVED_CHUNK>`) → `LLMAdapter.generate` → `evaluate` (DI, no global). If no retrieved → `INSUFFICIENT_EVIDENCE` without LLM call. Offline, deterministic, CPU-only.

### 3.12 Contradiction Engine

**Location:** `backend/app/evidence/contradictions.py:1` (Phase 9 complete)

- Deterministic `find_contradictions(evidence)` + `evaluate_with_contradictions` — no LLM, offline.
- Rules: 6 term pairs `normal/exceeds`, `within limit/above limit`, `operational/failed`, `healthy/fault`, `present/absent`, `replaced/pending` (phrase-aware, word-boundary) + numeric conflicts for same equipment/metric (P-204, pressure etc., unit-aware, diff>0.5 or >5%).
- Output `Contradiction{id, evidence_refs[2], conflicting_terms/metric/values, explanation}` + `ContradictionReport{has_contradictions, contradictions, evidence_state}` with `CONFLICTING_EVIDENCE` when found. Preserves `EvidenceRef` provenance, deduped, deterministic sort, handles empty/malformed.

### 3.13 Safety / Policy Layer

**Location:** `backend/app/safety/policy.py:1` (Phase 11 complete)

- Deterministic policy models `SafetyResult{allowed,reason,error_code}`, limits `objective 10..2000, steps/tool-calls 8, output 4000 chars, timeout 30s`.
- Validates tool allowlist (registry), path/file access (allow `data/raw|processed|tests/fixtures|tmp`, blocks traversal `..`, unauthorized), prompt-injection (`ignore previous instructions`, `system:`, etc.), code execution (`eval(`, `exec(`, `import os`, `subprocess`), network (`http://`, `api.openai.com`).
- Treats retrieved document contents as untrusted data — delimited evidence, never override system/tool rules. Safe failure: returns `ToolOutput`-like error without crash.

### 3.14 Audit System

**Location:** `backend/app/audit/logger.py:1` (Phase 11 complete)

- Append-only `audit_log(id,investigation_id,timestamp,event_type,tool,sanitized_input,success,execution_ms,evidence_refs,error_code)` in `data/workbench.db` (existing DB, no new DB). Sanitized inputs truncated 1000 chars, evidence refs truncated 2000 chars, no secrets.
- Helpers `log_event`, `get_audit_log`, `count_audit_logs`; integrated as `InvestigationOrchestrator → Safety → Tool Registry → Tool → Evidence` with events `investigation_start`, `plan_generated`, `tool_call`, `evidence_state`, `investigation_complete`, `safety_rejection`, `prompt_injection_detected`.
- Replay via `get_audit_log(investigation_id)` ordered by timestamp; deterministic, offline, no network.

### 3.15 Multimodal Layer (Phase 10)

**Location:** `backend/app/vision/adapter.py:1` (Phase 10 complete)

- `VisionAdapter` protocol `describe_image(image_path,prompt)->str`, `MockVisionAdapter` deterministic offline `[MOCK VISION]`, `OllamaVisionAdapter` localhost-only `httpx` to `vision_model llava:7b` (no download), config `vision_provider/mock`, `vision_model`, `vision_host`, `vision_timeout_s`, `vision_max_image_bytes`.
- Tool `backend/app/tools/inspect_image.py:1` validates `PNG/JPG/JPEG/WEBP` (magic + ext), 10 MB limit, path within `data/raw|processed|tests/fixtures|tmp`, SHA-256, provenance `EvidenceRef{filename,sha256,source_path}`, returns `ToolOutput{description,sha256}` as evidence (not fact).
- Integrates with `EvidenceRef`/`EvidenceGraph`/`EvidenceState` as image evidence; registered in `registry.py:inspect_image`, no new deps, no cloud, offline.

## 4. Module Layout (Planned)

```
backend/
├── app/
│   ├── main.py               # FastAPI app, lifespan, health (Phase 4+)
│   ├── config.py             # env loading via pydantic-settings — IngestionConfig
│   ├── api/
│   │   └── routes.py         # /ingest, /query, /investigations, /audit, /health (Phase 4+)
│   ├── ingestion/
│   │   ├── models.py         # NormalizedDocument, Page, Chunk, ChunkMetadata
│   │   ├── validator.py
│   │   ├── chunker.py
│   │   ├── pipeline.py
│   │   └── parsers/
│   │       ├── base.py       # Parser protocol + registry
│   │       ├── pdf_parser.py
│   │       ├── csv_parser.py
│   │       ├── json_parser.py
│   │       ├── txt_parser.py
│   │       └── md_parser.py
│   ├── retrieval/
│   │   ├── embedder.py       # EmbeddingAdapter + MockEmbedder (+ FastEmbed)
│   │   ├── vector_store.py   # VectorStore protocol + ChromaStore
│   │   └── retriever.py      # Phase 3
│   ├── llm/
│   │   ├── adapter.py        # Protocol definitions
│   │   ├── ollama_adapter.py
│   │   ├── llamacpp_adapter.py
│   │   └── mock_adapter.py
│   ├── tools/
│   │   ├── registry.py
│   │   ├── search_documents.py
│   │   ├── query_sensor_data.py
│   │   └── ...
│   ├── orchestrator/
│   │   └── investigation.py
│   ├── evidence/
│   │   ├── engine.py
│   │   └── contradiction.py
│   ├── safety/
│   │   └── policy.py
│   ├── audit/
│   │   └── logger.py
│   ├── multimodal/
│   │   └── vision_adapter.py # stub
│   └── store/
│       └── db.py             # SQLite init, migrations
├── requirements.txt
└── tests/  (mirrored in /tests)

├── scripts/
│       └── ingest.py         # CLI: python -m backend.scripts.ingest data/raw --recreate
├── requirements.txt
└── requirements-dev.txt

data/
├── raw/            # gitignored except samples/ (synthetic)
│   └── samples/    # committed synthetic fixtures
├── processed/      # gitignored — {doc_id}.json + {doc_id}.chunks.jsonl
├── vector_store/   # gitignored — Chroma persistence (chroma.sqlite3)
├── audit/          # gitignored — future audit JSONL
└── workbench.db    # gitignored — SQLite (documents, chunks)

models/             # gitignored when large; Ollama registry or GGUF files
```

## 5. Deployment on Target Machine

- No Docker/Kubernetes required. `python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000` + `npm run dev` (or built frontend served by FastAPI).
- Ollama (if chosen) runs as a local service on default port; adapter points to `http://localhost:11434`. Alternative: `llama.cpp` in-process — no daemon.
- Memory budget (indicative, to be benchmarked):
  - OS + browser: ~4 GB
  - Backend + vector store + SQLite: < 1 GB
  - Embedding model: ~0.2–0.5 GB
  - LLM (Q4 quantized 1.5–3B): ~2–4 GB
  - Headroom: ~6–8 GB — avoids swap on 16 GB.
- Startup order: DB/migrations → vector store → embedding model → LLM adapter health check → API ready.

## 6. Architecture Decisions (ADRs)

| # | Decision | Rationale | Status |
|---|----------|-----------|--------|
| ADR-001 | Single FastAPI process, no microservices | Fits 16 GB/no-GPU; reduces ops overhead; single port for demo | Accepted (Phase 1) |
| ADR-002 | Adapter pattern for LLM/embeddings/vision | Model choice deferred; benchmarking required; avoids provider lock-in | Accepted |
| ADR-003 | SQLite + Chroma/sqlite-vec over Postgres/pgvector | Zero extra service, single file, portable, CPU-only | Accepted |
| ADR-004 | Single orchestrator + deterministic tools over agent swarm | Predictable, testable, less RAM, clearer audit | Accepted |
| ADR-005 | Deterministic chunking, no LLM in ingestion | Reproducible, offline, testable | Accepted |
| ADR-006 | Vision as stub adapter until Phase 10 | Keeps Phase 1–9 focused; no rewrite later | Accepted |

## 7. What Is NOT in Architecture (Non-Goals)

- No cloud LLM in default path, no Kubernetes, no blockchain, no multi-tenant SaaS scaffolding, no GPU-dependent pipeline.

## 8. Next Step

Phase 11 complete — Safety policy + audit logger, orchestrator integration, 180 tests, offline. Next: Phase 12 — Judge-grade UI/demo.

---

**Last updated:** 2026-08-28 — Phase 11 complete. Safety policy + audit logger, 180 tests, offline.

## 9. Phase 2–11 ADRs

| # | Decision | Rationale | Status |
|---|----------|-----------|--------|
| ADR-007 | `pypdf` for PDF, `fastembed` (ONNX) preferred embedder, `chromadb` default vector store | Lightweight, Windows 3.13 wheels, no torch by default, swappable via adapter protocols | Accepted (Phase 2) |
| ADR-008 | Character-based chunking (800/120) with sentence-boundary preference | No tokenizer dep, deterministic, fits 16 GB | Accepted |
| ADR-009 | `EmbeddingAdapter` + `VectorStore` protocols in Phase 2 (ahead of Phase 3) | Keeps providers replaceable from day one; mock for tests, no network required | Accepted |
| ADR-010 | MiniLM via FastEmbed selected (Phase 3) | 1.0 vs 0.6 top-1, 98 vs 113 MB, 14 vs 69 ms, 10.3 vs 13.5s load | Accepted |
| ADR-011 | Retriever thresholds + metadata allowlist | Prevents low-score hits, enables equipment/file-type filtering, keeps provenance | Accepted |
| ADR-012 | LLM Adapter: Mock default, Ollama httpx localhost-only | Offline, swappable without orchestrator rewrite, 30s timeout, typed errors | Accepted (Phase 4) |
| ADR-013 | Evidence engine 4 states + DI | Citation/provenance gated, deterministic, CPU-only, offline | Accepted (Phase 5) |
| ADR-014 | Tool layer deterministic, registry allowlist, SQLite | Reuses Retriever/Chroma/workbench.db, Pydantic validation, no LLM/network, provenance preserved | Accepted (Phase 6) |
| ADR-015 | InvestigationOrchestrator bounded 8 steps, 30s, registry-only | LLM plans, code executes, evidence gating, persistence in workbench.db, MockAdapter offline | Accepted (Phase 7) |
| ADR-016 | EvidenceGraph deterministic Pydantic, 6 nodes/5 edges, provenance | No duplicate, invalid ref rejection, stable IDs, from_investigation, persistence, offline | Accepted (Phase 8) |
| ADR-017 | Contradiction engine deterministic term pairs + numeric | 6 pairs + same context numeric diff, provenance, no LLM, offline | Accepted (Phase 9) |
| ADR-018 | Vision adapter mock default, inspect_image tool | Local-only, SHA-256 provenance, PNG/JPG/WEBP, evidence integration, no heavy deps | Accepted (Phase 10) |
| ADR-019 | Safety policy + audit trail, orchestrator integration | Tool allowlist, path guards, injection/code/network detection, append-only audit in workbench.db, sanitized, offline | Accepted (Phase 11) |
