# ARCHITECTURE.md — SIH26117 Sovereign On-Premise Agentic AI Workbench

> **Status:** Phase 6 — Tool Layer (Complete)
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

**Planned location:** `backend/app/orchestrator/`

- **Single orchestrator** (not a swarm) — `InvestigationOrchestrator`.
- Flow:
  1. Parse objective → plan steps (LLM for planning, but plan is validated against tool allowlist).
  2. For each step: call tool → collect evidence → update evidence graph.
  3. After retrieval: run Evidence Engine + Contradiction Engine.
  4. Generate draft answer via LLM (grounded prompt: only use provided evidence).
  5. Evidence-gate the draft → assign `SUPPORTED | PARTIALLY_SUPPORTED | INSUFFICIENT_EVIDENCE | CONFLICTING_EVIDENCE`.
  6. Safety/policy check → add disclaimers or block unsafe advice.
  7. Persist audit trail → return structured response.
- ReAct-style loop but bounded (max steps, max tool calls) to stay within 16 GB RAM.

### 3.11 Evidence Engine

**Location:** `backend/app/evidence/engine.py:1` (Phase 5 complete)

- Inputs: retrieved chunks + LLM answer.
- Checks: keyword conflict (`replaced` vs `pending`, `normal` vs `exceeds`), citation parse `\[...\]`, invalid citation, per-claim coverage (split by sentence), provenance dedup.
- Output: `EvidenceResult{state: SUPPORTED|PARTIALLY_SUPPORTED|INSUFFICIENT_EVIDENCE|CONFLICTING_EVIDENCE, evidence_refs: EvidenceRef[], citations_found, invalid_citations, missing_coverage, conflicting}`.
- Integration: `answer_with_evidence(query, retriever, llm_adapter)` → `Retriever.retrieve` → `build_grounded_prompt` (delimited `<RETRIEVED_CHUNK>`) → `LLMAdapter.generate` → `evaluate` (DI, no global). If no retrieved → `INSUFFICIENT_EVIDENCE` without LLM call. Offline, deterministic, CPU-only.

### 3.12 Contradiction Engine

**Planned location:** `backend/app/evidence/contradiction.py`

- Pairwise comparison of evidence chunks that bear on the same fact (equipment/date/metric).
- Methods (progressive):
  - Phase 9a: rule-based (date/status/value mismatch) + embedding similarity for candidate pairs + LLM-assisted natural-language contradiction check (local model, grounded prompt).
  - Output: `Conflict { sources: [refA, refB], field, values, severity }`.
- Conflicts surface as `CONFLICTING_EVIDENCE` and are shown explicitly — never silently resolved.

### 3.13 Safety / Policy Layer

**Planned location:** `backend/app/safety/policy.py`

- SOP compliance checks (e.g., pressure thresholds, shutdown procedures).
- Safety-critical responses require: evidence + disclaimer + audit log entry.
- Prompt-injection defenses (see `docs/SECURITY.md`): input sanitization, instruction-hierarchy, output validation.
- Never allow the LLM to emit unvalidated operational directives.

### 3.14 Audit System

**Planned location:** `backend/app/audit/logger.py`

- Append-only log (SQLite `audit_log` + optional JSONL mirror `data/audit/*.jsonl`).
- Records: query, plan, tool calls (input/output truncated + hash), evidence refs, evidence state, contradiction flags, final response, timestamps.
- Replay: re-run investigation from logged inputs (deterministic tools) for accountability.

### 3.15 Multimodal Layer (Phase 10)

**Planned location:** `backend/app/multimodal/`

- Phase 1: stub `VisionAdapter` + `inspect_image` tool that returns `NOT_IMPLEMENTED` with audit entry.
- Phase 10: add `VisionAdapter` implementation (e.g., LLaVA / Qwen-VL quantized, or Moondream small) — same adapter pattern, same resource guard (swap text model out if RAM constrained).
- No architecture rewrite required: orchestrator already calls tools via adapter.

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

Phase 6 complete — 4 deterministic tools, registry allowlist, SQLite sensor/maintenance, 110 tests. Next: Phase 7 — Investigation Mode (orchestrator planning, bounded execution, audit).

---

**Last updated:** 2026-08-28 — Phase 6 complete. Added tool layer, registry, sensor/maintenance SQLite, 110 tests.

## 9. Phase 2–6 ADRs

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
