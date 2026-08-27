# ARCHITECTURE.md — SIH26117 Sovereign On-Premise Agentic AI Workbench

> **Status:** Phase 2 — Local Document Ingestion (Complete)
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

**Planned location:** `backend/app/llm/`

**Adapter interface (planned — see `AGENTS.md:6`):**
```python
class LLMAdapter(Protocol):
    def generate(self, prompt: str, system: str | None, **kwargs) -> str: ...
    async def generate_stream(self, prompt: str, system: str | None, **kwargs) -> AsyncIterator[str]: ...
class EmbeddingAdapter(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...

class VisionAdapter(Protocol):  # stub until Phase 10
    def describe_image(self, image_bytes: bytes, prompt: str) -> str: ...
```

- **Implementations:**
  - `OllamaAdapter` — primary for dev (Ollama serves quantized GGUF models via local HTTP).
  - `LlamaCppAdapter` — alternative (direct `llama.cpp` Python bindings, no daemon).
  - `MockAdapter` — deterministic fixture for tests/CI (no model needed).
  - Optional non-default `CloudAdapter` — only if explicitly enabled via `ALLOW_CLOUD_ADAPTER=true` for benchmarking; never in default path.
- Model choice deferred until benchmarking. Candidates (quantized Q4_K_M, CPU-friendly): Qwen2.5 1.5B/3B, Phi-3 mini 3.8B, Gemma 2 2B, Llama 3.2 1B/3B. See `docs/SECURITY.md` for isolation implications.
- Configuration via `config.yaml` / `.env`: `LLM_ADAPTER`, `LLM_MODEL`, `EMBEDDING_MODEL`, `OLLAMA_HOST`. No code change to swap adapters.
- Resource guard: single model resident at a time by default; no concurrent multi-LLM on 16 GB RAM. Document memory budget per model.

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

**Planned location:** `backend/app/retrieval/embedder.py`

- Local embedding model via `EmbeddingAdapter` (same adapter pattern).
- Candidate (benchmark later): `sentence-transformers/all-MiniLM-L6-v2`, `bge-small-en-v1.5`, or `nomic-embed-text` via Ollama — small, CPU-friendly, < 150 MB.
- Batch embedding at ingest; single-text embedding at query.
- No network call.

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

**Planned location:** `backend/app/retrieval/retriever.py`

- Query → `EmbeddingAdapter.embed` → vector search (top-k, e.g., k=6–8) → optional metadata filter (equipmentId, date range) → rerank (simple cosine or lightweight cross-encoder later).
- Returns `RetrievedChunk[]` with scores + provenance (doc/page/chunkId).
- No LLM in retrieval; deterministic.

### 3.9 Tool Layer

**Planned location:** `backend/app/tools/` — see `docs/AGENT_SPEC.md` for full contracts.

- Deterministic Python functions, not LLM-generated code execution.
- Each tool: Pydantic `Input`/`Output`, input validation, permission check, evidence attachment, audit log write.
- Planned tools: `search_documents`, `retrieve_evidence`, `query_sensor_data`, `search_maintenance_logs`, `inspect_image` (Phase 10 stub), `compare_sources`, `create_report`.
- Orchestrator decides *which* tool and *with what args*; tool code decides *how* to execute.

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

**Planned location:** `backend/app/evidence/engine.py`

- Inputs: draft answer claims + retrieved chunks.
- Checks: citation coverage, quote grounding, numeric consistency.
- Output: per-claim verdict + overall `EvidenceState` + missing-evidence list.
- Evidence-gating is mandatory — no answer is returned as fact without it.

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

Phase 2 complete — 6 file types ingesting locally with full provenance, MockEmbedder default, ChromaStore, 33 tests passing. Next: Phase 3 — Local embeddings + retrieval (benchmark FastEmbed bge-small vs MiniLM, implement retriever, evidence trace).

---

**Last updated:** 2026-08-27 — Phase 2 complete. Implemented ingestion pipeline, 6 parsers, chunker, MockEmbedder, ChromaStore, CLI, synthetic flagship samples.

## 9. Phase 2 ADRs

| # | Decision | Rationale | Status |
|---|----------|-----------|--------|
| ADR-007 | `pypdf` for PDF, `fastembed` (ONNX) preferred embedder, `chromadb` default vector store | Lightweight, Windows 3.13 wheels, no torch by default, swappable via adapter protocols | Accepted (Phase 2) |
| ADR-008 | Character-based chunking (800/120) with sentence-boundary preference | No tokenizer dep, deterministic, fits 16 GB | Accepted |
| ADR-009 | `EmbeddingAdapter` + `VectorStore` protocols in Phase 2 (ahead of Phase 3) | Keeps providers replaceable from day one; mock for tests, no network required | Accepted |
