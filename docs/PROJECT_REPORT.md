# SIH26117 — Sovereign On-Premise Agentic AI Workbench — Complete Project Report (A to Z)

> **Status:** Phase 12 Complete (Final) · **Stack:** FastAPI + SQLite + Chroma + FastEmbed (MiniLM) + Mock/Ollama + Vite+React · **Mode:** Local-only, offline-capable, evidence-gated

---

## 1. Executive Summary

**What it is:** An on-premise incident investigation workbench for confidential industrial settings (refineries, manufacturing, energy). It turns a natural-language objective like *“Investigate abnormal pressure event in Pump P-204 on 2026-08-15”* into a structured, auditable report with explicit evidence state.

**What it is not:** Not a generic chatbot, not a cloud RAG demo, not an AI toy.

**Users:** Reliability/maintenance engineer, safety/operations engineer, plant manager/auditor, SIH judges.

**Differentiators (all implemented):**
- **Investigation Mode** — bounded orchestrator plans and executes tools
- **Evidence Graph** — Investigation→Step→Tool→Evidence→Document→Chunk
- **Contradiction Detection** — surfaces conflicting sources
- **Evidence-Gated Responses** — `SUPPORTED | PARTIALLY_SUPPORTED | INSUFFICIENT_EVIDENCE | CONFLICTING_EVIDENCE`
- **Audit/Replay Trail** — append-only, deterministic

**Flagship scenario:** `data/raw/samples/` — `SOP_P-204.pdf` (normal 2.1–3.4 bar), `sensor_P-204_2026-08-15.json` (4.8–4.9 bar spike), `maintenance_log_P-204.csv` (valve replaced 2026-08-12) vs `checklist_P-204.md` (pending 2026-08-14) → must return `CONFLICTING_EVIDENCE` for valve, `SUPPORTED` for exceedance, 19-node graph, SOP §4.2 disclaimer.

**LLM role:** Reasoning/orchestration only. Deterministic code owns retrieval, tool execution, evidence gating, audit.

---

## 2. Principles & Hardware

**Principles:** Local-first, model-agnostic (adapter pattern), lightweight, auditable. No cloud AI in default path, no Kubernetes/microservices/blockchain, no multi-LLM concurrency.

**Hardware (non-negotiable):** Windows 11, Intel 12th-gen mobile, 16 GB RAM, Intel Iris Xe (no NVIDIA), 512 GB NVMe, Python 3.13.2, Node 24.19.0. No CUDA/24 GB assumption. Quantized models `Q4_K_M` (Qwen2.5 1.5B/3B, Phi-3 mini deferred), `MockAdapter` default, swapped via config not code.

**Memory budget (`docs/ARCHITECTURE.md:299`):** OS+browser ~4 GB + backend/Chroma/SQLite <1 GB + embedding 0.2–0.5 GB + LLM Q4 1.5B ~1.5 GB = 6–8 GB headroom.

**Config single source:** `backend/app/config.py:17` `IngestionConfig(BaseSettings, env_file=".env")` + `get_config()/reset_config()` singleton. Defaults in `.env.example:1`.

---

## 3. High-Level Data Flow

```
User
 ↓
Workbench UI  frontend/  Vite 5 + React 18  6 views (Overview/Investigations/Documents/Evidence/Audit/Settings)
 ↓ HTTP/JSON  same-origin  Vite proxy /api → localhost:8000  prod: FastAPI mounts frontend/dist
Backend API  backend/app/api/routes.py  +  backend/app/main.py lifespan
 ↓
InvestigationOrchestrator  backend/app/orchestrator/investigation.py  MAX_STEPS=8 STEP_TIMEOUT=30s MAX_TOOL_CALLS=8
 ├── Retriever ─────────→ Vector Store (Chroma data/vector_store/) + Structured Store (SQLite data/workbench.db)
 ├── Tool Layer ────────→ Deterministic local tools (no network/shell)
 ├── Evidence Engine ───→ 4 states + citation coverage
 ├── Contradiction Engine → 6 term-pairs + numeric
 ├── Safety/Policy ─────→ SOP gating + disclaimer
 └── LLM Adapter ───────→ Ollama / llama.cpp / Mock (local only)
 ↓
Validated Response (answer + evidenceRefs + evidenceState + auditId)
 ↓
EvidenceGraph + Audit Trail  (persisted locally, replayable)
 ↓
UI (findings, evidence cards, graph, audit)
```

**Offline invariant:** `internet_disabled == true` → entire flow still runs.

---

## 4. Configuration

`backend/app/config.py:17` — `IngestionConfig` fields (override via `.env`):

| Key | Default | Notes |
|-----|---------|-------|
| `raw_dir` `processed_dir` `vector_store_dir` `db_path` | `data/raw` `data/processed` `data/vector_store` `data/workbench.db` | `_project_root()` relative |
| `allowed_extensions` `max_file_size_bytes` `max_batch_size_bytes` | `pdf,csv,json,txt,log,md` 50 MB 500 MB | `validator.py` |
| `chunk_size` `chunk_overlap` | 800 120 | `chunker.py` |
| `embedding_provider` `embedding_model` `embedding_dim` | `mock` `all-MiniLM-L6-v2` 384 | `retrieval/embedder.py:10` |
| `vector_store_provider` `vector_store_collection` | `chroma` `chunks` | `retrieval/vector_store.py` |
| `retrieval_top_k` `retrieval_threshold` | 6 0.0 | `retriever.py` |
| `llm_provider` `llm_model` `llm_host` `llm_timeout_s` `llm_max_tokens` `llm_temperature` `allow_cloud_adapter` | `mock` `qwen2.5:1.5b-instruct-q4_K_M` `http://localhost:11434` 30 512 0.2 false | `llm/adapter.py` |
| `vision_provider` `vision_model` `vision_host` `vision_timeout_s` `vision_max_image_bytes` | `mock` `llava:7b` `localhost:11434` 30 10 MB | `vision/adapter.py` |

`.env` is gitignored, `.env.example:1` is committed safe defaults.

---

## 5. Data & Ingestion

**Samples:** `data/raw/samples/` 6 files + `tests/fixtures/` mirror:
- `SOP_P-204.pdf` 2,462 B — 2.1–3.4 bar, §4.2 shutdown
- `maintenance_log_P-204.csv` — 2026-08-10 seal, 2026-08-12 valve, 2026-08-13 test
- `sensor_P-204_2026-08-15.json` — 2.3/3.1/4.8/4.9 bar
- `system_events.log` — WARN 4.8 EXCEEDS, vibration high, auto-shutdown
- `operator_note_2026-08-15.txt` — 14:35 vibration 4.8 bar
- `checklist_P-204.md` — pending (contradicts maintenance)
- `evil.xml` (rejection test)

**Pipeline:** `backend/scripts/ingest.py:16` CLI `python -m backend.scripts.ingest [input] --recreate --no-embed --recursive`

1. **Validation** `ingestion/validator.py:35` — null/traversal before existence, allowlist, size, magic `%PDF`, `compute_sha256` 8192B streaming
2. **Parse** `ingestion/parsers/*.py` — `PdfParser` (pypdf), `CsvParser`, `JsonParser`, `TxtParser`, `MdParser` → `NormalizedDocument` `ingestion/models.py:17`
3. **Chunk** `ingestion/chunker.py:9` — sliding 800/120, `_find_break` prefers `\n\n`/`\n`/`. ` in last 20%, `chunk_id doc:pN:cXXXX`, line_range via bisect
4. **Persist** `ingestion/pipeline.py:32` — `data/processed/{doc_id}.json` + `{doc_id}.chunks.jsonl` + SQLite `store/db.py` `insert_document/insert_chunks`
5. **Embed** `retrieval/embedder.py:30` `MockEmbedder` hash→L2 or `FastEmbedEmbedder` → `vector_store.py:32` `ChromaStore.PersistentClient` upsert metadatas

`IngestionReport` per-file `IngestResult` isolation; embedding failure non-fatal.

---

## 6. Retrieval

`retrieval/retriever.py:25` — `Retriever.retrieve(query, top_k 1..20 default 6, threshold 0..1 default 0.0, filters allowlist)`:

`query` (1..2000) → `embedder.embed` → `ChromaStore.search` → `score=1-distance` → threshold if `>0` → `RetrievedChunk(chunk_id,text,score,distance,metadata{filename,chunk_id,document_id,sha256,page_number,source_path})` provenance preserved.

`retrieval/embedder.py:10` — `EmbeddingAdapter`; `MockEmbedder` deterministic, `FastEmbedEmbedder` lazy `TextEmbedding`. Benchmark `docs/BENCHMARK_EMBEDDINGS.md:10` — MiniLM 10.3s/98MB/14ms top-1 1.0 vs BGE 13.5s/113MB/69ms 0.6 → selected MiniLM.

`retrieval/vector_store.py:32` — `ChromaStore` `PersistentClient`, `upsert/search/delete/count`, single file `chroma.sqlite3`.

---

## 7. LLM Layer

`llm/adapter.py:43` — `LLMAdapter(generate, generate_stream, health_check)` + validators `validate_prompt 8000`/`validate_system 2000` + errors `LLMError/Timeout/Unavailable`.

`llm/factory.py:8` — `get_llm_adapter()` reads `llm_provider` → `mock|ollama|llamacpp→NotImplemented`.

`llm/mock_adapter.py:9` — canned substring, planner JSON when `system` contains `industrial investigation planner` (pressure→search_documents+sensor, valve→search+maintenance), grounded citation `Evidence shows ... [file]` when `<RETRIEVED_CHUNK>` present.

`llm/ollama_adapter.py:12` — local HTTP `http://localhost:11434`, guard `allow_cloud_adapter=false` blocks non-local host, `httpx.Client POST /api/generate {model,prompt,stream:false, options{temperature,num_predict}, system}`, `health_check GET /api/tags`.

---

## 8. Evidence & Contradiction

`evidence/engine.py:14` — `EvidenceState`, `EvidenceRef`, `EvidenceResult`, `CITATION_RE \[([^\[\]]+)\]`

- `parse_citations`, `_chunk_lookup`, `map_citations` (exact→token `[\s,:;]`→substring), `split_claims` (`[.!?]\s+`), `detect_conflicting_evidence` (replaced/pending, normal/exceeds)
- `evaluate` priority: no retrieved→INSUFFICIENT, `insufficient evidence` phrase→INSUFFICIENT, conflicting→CONFLICTING, no citations→INSUFFICIENT, all invalid→INSUFFICIENT, some invalid→PARTIALLY, per-claim `all cited→SUPPORTED` else PARTIALLY
- `build_grounded_prompt` — strict system listing `allowed_filenames` only, example, 1-3 sentences, evidence block `<RETRIEVED_CHUNK id file>`
- `repair_missing_citations` — if no valid citation but evidence exists, append per-claim `[filename]` round-robin (never invent)
- `answer_with_evidence` — retrieve→prompt→generate→repair→evaluate; no retrieved→no LLM call

`evidence/contradictions.py:29` — 6 term-pairs (`normal/exceeds`, `within limit/above limit`, `operational/failed`, `healthy/fault`, `present/absent`, `replaced/pending`) + numeric same-context (`P-204`, metric, diff>0.5 or >5%), deterministic sha256 ids.

`evidence/graph.py:120` — `NodeType` 6, `EdgeType` 5, `EvidenceGraph.from_investigation` builds Investigation CONTAINS Step/Claim, Step USED_TOOL Tool, Step PRODUCED Chunk, Claim SUPPORTED_BY Chunk, `save_to_db/load_from_db` via `evidence_graph_nodes/edges`.

---

## 9. Tool Layer

`tools/registry.py:12` — `TOOL_REGISTRY` 5 tools, `execute_tool` validates via Pydantic then handler.

`tools/common.py` — `ToolOutput(success,result,evidence_refs,error,execution_ms)`.

| Tool | Input | Output |
|------|-------|--------|
| `search_documents` `tools/search_documents.py:1` | `query 1..500, top_k 1..20, filters` | `RetrievedChunk` list + refs |
| `retrieve_evidence` `tools/retrieve_evidence.py:1` | `chunk_id XOR document_id` regex | `ChunkDetail` with provenance |
| `query_sensor_data` `tools/query_sensor_data.py:1` | `equipment_id, metric, start/end, aggregation raw|avg|max|min` | rows + EvidenceRef |
| `search_maintenance_logs` `tools/search_maintenance_logs.py:1` | `equipment_id, date range, keyword` | rows + refs |
| `inspect_image` `tools/inspect_image.py:1` | `image_path, prompt` (magic PNG/JPG/WEBP 10 MB, roots `data/raw|processed|fixtures`) | description + refs via `VisionAdapter` |

All parameterized SQLite, no network/shell, offline.

---

## 10. Orchestrator (Investigation Mode)

`orchestrator/investigation.py:97` — `MAX_STEPS=8 STEP_TIMEOUT=30s MAX_TOOL_CALLS=8 OBJECTIVE 10..2000`

Models: `InvestigationStep`, `InvestigationPlan` (sequential 1..N + per-step `TOOL_REGISTRY[input_model]` validation), `StepExecution`, `InvestigationReport`.

Planning: `PLANNING_SYSTEM` JSON-only, `PLANNING_PROMPT_TEMPLATE` 5 tools, `_extract_json` handles fences, `generate(temperature=0.0, max_tokens=800)` → `InvestigationPlan` → audit `plan_generated`.

Execution: sorted `step_no`, per-step `safety_validate_tool_name/input` + `check_step_limits` + `check_output_size` 4000 + `detect_prompt_injection`; `execute_tool` with injected `retriever/db_path`; fallback `chunk_id`; audit `tool_call`; timeout.

Summary: pseudo `RetrievedChunk` from `all_evidence` → `build_grounded_prompt` → `generate` → `repair_missing_citations` → `evaluate` → `CONFLICTING` precedence → `completed|partial|failed` + persist `investigations`/`investigation_steps` + audit `evidence_state`/`investigation_complete`.

---

## 11. Safety & Audit

`safety/policy.py:20` — `INJECTION_PATTERNS` 11, `CODE_PATTERNS`, `NETWORK_PATTERNS`, `sanitize_text`, `detect_*`, `validate_objective/tool_name/path/tool_input`, `check_output_size/step_limits`. Treats docs as untrusted, instruction hierarchy, delimiting.

`audit/logger.py:14` — append-only `audit_log(id, investigation_id, timestamp, event_type, tool, sanitized_input 1000, success, execution_ms, evidence_refs 2000, error_code)` indexed, `log_event` hash id, `get_audit_log/count`, events `investigation_start/safety_rejection/plan_generated/tool_call/evidence_state/investigation_complete`.

`safety` + `vision/adapter.py` — `MockVisionAdapter` vs `OllamaVisionAdapter` base64 local.

---

## 12. Backend API

`app/main.py:14` — lifespan `init_db+audit+investigations`, `FastAPI v12.0.0` CORS `*`, `include_router /api`, mount `frontend/dist` if exists.

`app/api/routes.py:21` — 10 endpoints:

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/health` `/api/status` | docs/chunks/vector/investigations/offline/mock |
| GET | `/api/documents` | library |
| POST | `/api/ingest` | multipart, 413 if >50 MB |
| POST | `/api/investigations` | `{objective≥10}` → report |
| GET | `/api/investigations/{id}` | report + steps |
| GET | `/api/investigations/{id}/graph` | `EvidenceGraph` |
| GET | `/api/investigations/{id}/audit` | events |
| POST | `/api/query` | `answer_with_evidence+contradictions` |
| GET | `/api/evidence/{chunk_id}` | chunk + doc |

---

## 13. Frontend

`frontend/package.json:1` Vite 5 + React 18, `vite.config.js:6` port 5173 proxy `/api`→8000, `index.html` → `main.jsx` → `App.jsx:66` 6 views + `App.css:1` design system (`--accent #0f766e`, `--bg #f8fafc`, 8px, shadows).

**Shell:** top bar brand + `Local processing · Evidence validation · Audit` + investigation context; sidebar 220px (Overview, Investigations, Documents, Evidence, Audit Trail, Settings).

**Views:** Overview KPIs + recent investigations/alerts; Investigations composer + 4 presets + timeline + result centerpiece (badge, findings with clickable `[file]`→Evidence, conflicts side-by-side); Documents searchable grid + drag-drop `/api/ingest`; Evidence cards (file/page/excerpt/score, provenance expandable) + graph (legend, 18 nodes, 12 edges); Audit human-readable expandable; Settings local-only.

Build `dist` 175 kB JS (53 gz) + 17 kB CSS, offline, no new deps.

---

## 14. Storage

`store/db.py:10` — SQLite `data/workbench.db` WAL+FK: `documents`, `chunks`, `sensor_events`, `maintenance_logs`, `investigations`, `investigation_steps`, `audit_log`, `evidence_graph_nodes/edges`. Plus `data/processed/{doc_id}.json+.chunks.jsonl`, `data/vector_store/chroma.sqlite3`.

---

## 15. Testing

`tests/` 192 tests — `conftest.py` root insert, fixtures mirror samples:

`api 5, chunker 6, contradictions 17, embedder 6, evidence_engine 19, evidence_graph 12, investigation 14, llm_adapter 7, llm_factory 6, llm_ollama 6, parsers 8, pipeline 2, qwen_repair 7, retriever 7, safety_audit 14, tools 32, validator 8, vector_store 3, vision 13` → `$env:PYTHONPATH="D:\SIH26117"; python -m pytest -q` uses `MockAdapter/MockEmbedder`, no network.

---

## 16. Security Model

`docs/SECURITY.md` — no cloud in default path, `ALLOW_CLOUD=false` guard, health `offline:true`, path `Path.resolve under data/raw|processed|fixtures`, file-type/SHA, no `os.system/subprocess/eval`, instruction hierarchy + delimiting `<RETRIEVED_CHUNK>`, prompt/code/network detection, evidence gating 4 states, SOP disclaimer, append-only audit.

---

## 17. Deployment & Run

`RUN.md` + `README.md` — `pip install -r backend/requirements.txt` + `requirements-dev.txt`, `npm --prefix frontend install && npm --prefix frontend run build`, `python -m backend.scripts.ingest data/raw/samples --recreate`, `$env:PYTHONPATH="D:\SIH26117"; python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload` + `npm --prefix frontend run dev` → `http://127.0.0.1:5173`, verify `Invoke-RestMethod /api/health` + `pytest -q`. Memory budget 6–8 GB headroom. Offline after setup.

---

## 18. What Is Not Included

No cloud LLM default, no Kubernetes/blockchain, no multi-tenant SaaS, no GPU assumption, no real-time streaming, no untested security claims, no real confidential data (synthetic only, gitignored).

---

**Last updated:** 2026-08-29 — Full A-to-Z report for Phase 12 industrial workbench (192 tests, 175 kB UI, offline mock default, Ollama Qwen 1.5B optional).
