"""FastAPI main — single process, local-only."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from backend.app.api.routes import router
from backend.app.store.db import init_db

app = FastAPI(title="Sovereign Workbench", version="12.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup():
    init_db()
    # ensure audit/investigation tables
    try:
        from backend.app.audit.logger import _ensure_audit_table
        _ensure_audit_table()
    except Exception:
        pass
    try:
        from backend.app.orchestrator.investigation import _ensure_investigation_tables
        _ensure_investigation_tables()
    except Exception:
        pass

app.include_router(router, prefix="/api")

@app.get("/")
def root():
    return {"status": "ok", "message": "Sovereign Workbench API", "docs": "/docs"}

# Serve frontend static if built
frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
