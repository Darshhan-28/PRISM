# RUN.md — How to Run SIH26117 Sovereign Workbench

> **See also:** `README.md` (overview) · `docs/ARCHITECTURE.md` (design) · `.env.example` (config defaults)

Single-machine, offline-capable industrial investigation workbench. No cloud, no Docker, no GPU required.

## 1. Prerequisites

- **OS:** Windows 11 (tested) — macOS/Linux also works
- **Hardware:** 16 GB RAM, Intel Iris Xe or similar, 512 GB storage — no NVIDIA GPU needed
- **Software:** Python 3.13, Node 24, Git
- **Ports:** `8000` (FastAPI) and `5173` (Vite) must be free
- **Network:** Internet required only for one-time setup; demo runs fully offline after that

## 2. One-time setup (needs internet)

From repo root `D:\SIH26117`:

```powershell
# Python deps (from backend/requirements.txt and backend/requirements-dev.txt)
pip install -r backend/requirements.txt
pip install -r backend/requirements-dev.txt  # tests only

# Frontend deps (from frontend/package.json and frontend/vite.config.js)
npm --prefix frontend install
npm --prefix frontend run build              # produces frontend/dist (gitignored)

# Seed synthetic data (6 samples in data/raw/samples)
python -m backend.scripts.ingest data/raw/samples --recreate
```

`--recreate` clears `data/processed`, `data/vector_store`, `data/workbench.db` then re-ingests. Omit it for incremental ingest.

Optional — local model (not required, mock is default):
```powershell
# Install Ollama from https://ollama.com, then:
ollama pull qwen2.5:1.5b-instruct
Copy-Item .env.example .env
# edit .env: set LLM_PROVIDER=ollama  LLM_MODEL=qwen2.5:1.5b-instruct  LLM_HOST=http://localhost:11434
```

## 3. Run (offline after setup)

Open **two** terminals at repo root.

**Terminal 1 — Backend API** (from `backend/app/main.py` lifespan, single process):
```powershell
$env:PYTHONPATH="D:\SIH26117"; python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
# production (no auto-reload): omit --reload
```

**Terminal 2 — Frontend** (from `frontend/vite.config.js` — proxies `/api` → `http://localhost:8000`):
```powershell
npm --prefix frontend run dev
```

Open:
- **UI:** http://127.0.0.1:5173  (Vite dev, recommended)
- **API docs:** http://127.0.0.1:8000/docs
- **Health:** http://127.0.0.1:8000/api/health

> `frontend/dist` is gitignored and served only if built; Vite dev does not need it. `backend/app/main.py` mounts `frontend/dist` if it exists.

## 4. Verify

```powershell
# API health (from backend/app/api/routes.py)
Invoke-RestMethod http://127.0.0.1:8000/api/health | ConvertTo-Json

# Tests & build (from backend/requirements-dev.txt, frontend/package.json)
$env:PYTHONPATH="D:\SIH26117"; python -m pytest -q          # 192 passed
npm --prefix frontend run build                             # 175 kB JS
```

## 5. Demo flow (2 clicks, no docs needed)

1. **Overview** → check `Documents indexed` and `System status` (Local processing · Evidence validation · Audit).
2. **Investigations** → `What would you like to investigate?` is pre-filled:
   `Investigate abnormal pressure event in Pump P-204 on 2026-08-15`
   Click **Start investigation** (or a preset: Pressure anomaly / Equipment maintenance / Sensor deviation / Safety review).
3. **Result** → read **Findings** with status badge `Supported` / `Partially supported` / `Conflicting evidence` / `Insufficient evidence`. Click a citation `[SOP_P-204.pdf]` to jump to **Evidence**.
4. **Evidence** → cards show file, page, excerpt, relevance; expand **Source details** for SHA-256/provenance; conflicts shown side-by-side.
5. **Documents** → drag-and-drop upload (uses `POST /api/ingest`) or search library.
6. **Audit Trail** → expand events for technical details.

## 6. CLI ingest variants (from `backend/scripts/ingest.py`)

```powershell
python -m backend.scripts.ingest data/raw/samples --recreate   # full reset
python -m backend.scripts.ingest data/raw/my.pdf              # single file
python -m backend.scripts.ingest --no-embed                   # skip vectors
```

## 7. Configuration (from `.env.example` and `backend/app/config.py`)

Copy `.env.example` → `.env` and override only needed keys. Defaults are safe and offline:

- `LLM_PROVIDER=mock` (or `ollama`), `LLM_MODEL=qwen2.5:1.5b-instruct-q4_K_M`, `LLM_HOST=http://localhost:11434`, `ALLOW_CLOUD_ADAPTER=false`
- `EMBEDDING_PROVIDER=mock` / `fastembed`, `VECTOR_STORE_PROVIDER=chroma`
- `VISION_PROVIDER=mock`

Never commit `.env` (gitignored) or real data (`data/raw/*` except `samples` is gitignored).

## 8. Endpoints (from `backend/app/api/routes.py`)

`GET /api/health` · `GET /api/status` · `GET /api/documents` · `POST /api/ingest` · `POST /api/investigations` · `GET /api/investigations/{id}` · `GET /api/investigations/{id}/graph` · `GET /api/investigations/{id}/audit` · `POST /api/query` · `GET /api/evidence/{chunk_id}`

## 9. Troubleshooting

- `ModuleNotFoundError: backend` → set `$env:PYTHONPATH="D:\SIH26117"` before `uvicorn`/`pytest`
- Port in use → change `--port` / `vite.config.js` `server.port`
- `LLMUnavailableError` → `ollama serve` not running or model not pulled; keep `LLM_PROVIDER=mock` for offline demo
- Empty evidence → re-run ingest with `--recreate` and check `data/raw/samples` exists

## 10. Stop

`Ctrl+C` both terminals. No data leaves the machine. `data/workbench.db` and `data/vector_store` remain local for next run.
