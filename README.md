# PRISM - Sovereign Agentic AI Workbench

> **Run instructions:** See [RUN.md](RUN.md) for step-by-step setup, run, and verify.

Offline, evidence-backed industrial investigation workbench. Runs fully local on 16 GB / Iris Xe laptop, no cloud AI.

**Stack:** FastAPI + SQLite + Chroma + FastEmbed (MiniLM) + Mock/Ollama adapters + Vite+React (offline build).

## Quick Demo (Judge Flow)

**Prereqs:** Python 3.13, Node 24, no Docker/GPU needed. Models are mock default — no download.

**1) Setup (one-time, needs internet for pip/npm):**
```powershell
pip install -r backend/requirements.txt
pip install -r backend/requirements-dev.txt  # tests only
npm --prefix frontend install
npm --prefix frontend run build
# Seed synthetic samples (already in data/raw/samples)
python -m backend.scripts.ingest data/raw/samples --recreate
```

**2) Run Offline Demo (Wi-Fi can be disabled after setup):**
```powershell
# Backend API (serves frontend build too)
$env:PYTHONPATH="D:\SIH26117"; python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
# Frontend dev (alternative, proxied)
# npm --prefix frontend run dev
```

**3) Open:** http://127.0.0.1:8000 (API) or http://127.0.0.1:5173 (Vite dev) — shows Dashboard.

**4) Judge Click Path (2 clicks, 30 seconds):**
- Dashboard shows `documents 6, investigations 0, offline: yes`
- Evidence Workspace → objective pre-filled: `Investigate abnormal pressure event in Pump P-204 on 2026-08-15` → **Run Investigation**
- Observe: **Answer** with `SUPPORTED/PARTIALLY_SUPPORTED` badge + **Evidence Panel** (filename, page/line, score, snippet, SHA-256) + **Contradiction Panel** (e.g., valve `replaced` vs `pending` → `CONFLICTING_EVIDENCE`) + **Evidence Graph** (Investigation→Steps→Tools→Documents→Chunks, 19 nodes) + **Audit/Safety Panel** (investigation_start, tool_call, safety_rejection)

**Demo Data (synthetic, works immediately):** `data/raw/samples/SOP_P-204.pdf` (2.1–3.4 bar), `maintenance_log_P-204.csv`, `sensor_P-204_2026-08-15.json` (4.8 bar spike), `system_events.log`, `checklist_P-204.md` — triggers flagship contradiction (valve replaced 08-12 vs pending 08-14).

**5) API Smoke (offline):**
```powershell
$env:PYTHONPATH="D:\SIH26117"; python -c "from fastapi.testclient import TestClient; from backend.app.main import app; c=TestClient(app); print(c.get('/api/health').json()); print(c.post('/api/investigations', json={'objective':'Investigate pressure in P-204 for demo test case long enough'}).json()['evidence_state'])"
```

**6) Tests & Build:**
```powershell
$env:PYTHONPATH="D:\SIH26117"; python -m pytest tests -q  # 180 tests
npm --prefix frontend run build  # produces frontend/dist (154 kB gz 49 kB)
```

## API Endpoints

- `GET /api/health` / `/api/status` — documents/chunks/vector_store/investigations counts, offline flag
- `GET /api/documents` — indexed docs
- `POST /api/ingest` — file upload (multipart) → validated → chunked → embedded → Chroma
- `POST /api/investigations` — `{objective}` → bounded orchestrator (MAX_STEPS=8, 30s, registry-only) → `InvestigationReport{evidence_state, summary, steps_executed, evidence_refs}`
- `GET /api/investigations/{id}` — investigation + steps
- `GET /api/investigations/{id}/graph` — `EvidenceGraph{nodes,edges}`
- `GET /api/investigations/{id}/audit` — append-only `audit_log`
- `POST /api/query` — `answer_with_evidence` + contradictions
- `GET /api/evidence/{chunk_id}` — chunk detail

All offline, MockAdapter default, local-only (`allow_cloud_adapter=false`).

## Hardware Fit

16 GB RAM: OS 4 GB + backend 1 GB + embedding 0.2 GB + LLM Q4 1.5B 1.5 GB = headroom 6–8 GB. No CUDA, no Docker, single process.

## Docs

- `AGENTS.md` — persistent engineering rules
- `docs/ARCHITECTURE.md` — target architecture (Phase 12 complete)
- `docs/PRODUCT_SPEC.md` — flagship scenario
- `docs/AGENT_SPEC.md` — tool contracts
- `docs/SECURITY.md` — sovereignty, offline, injection guards
- `docs/BENCHMARK_EMBEDDINGS.md` — MiniLM selection

## Limitations

- LLM is mock by default (deterministic `[MOCK VISION]` + SOP heuristics); real local model requires `ollama pull qwen2.5:1.5b-instruct-q4_K_M` + `LLM_PROVIDER=ollama` (not bundled).
- No auth (single-workstation demo, bind `127.0.0.1`).
- Vision is mock description, not real LLaVA.
