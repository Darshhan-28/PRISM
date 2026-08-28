"""Central config — single source of truth for ingestion/retrieval paths and limits.

All values overridable via .env using pydantic-settings.
No hardcoded secrets.
"""

from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    # backend/app/config.py -> backend/app -> backend -> root
    return Path(__file__).resolve().parents[2]


class IngestionConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Paths (relative to project root)
    raw_dir: Path = Field(default_factory=lambda: _project_root() / "data" / "raw")
    processed_dir: Path = Field(default_factory=lambda: _project_root() / "data" / "processed")
    vector_store_dir: Path = Field(default_factory=lambda: _project_root() / "data" / "vector_store")
    db_path: Path = Field(default_factory=lambda: _project_root() / "data" / "workbench.db")

    # Validation
    allowed_extensions: set[str] = Field(default_factory=lambda: {"pdf", "csv", "json", "txt", "log", "md"})
    max_file_size_bytes: int = 50 * 1024 * 1024  # 50 MB
    max_batch_size_bytes: int = 500 * 1024 * 1024  # 500 MB

    # Chunking
    chunk_size: int = 800
    chunk_overlap: int = 120

    # Providers (replaceable via env)
    embedding_provider: str = Field(default="mock")  # mock | fastembed | sentence_transformers
    embedding_model: str = Field(default="sentence-transformers/all-MiniLM-L6-v2")  # Benchmark 2026-08-28: MiniLM wins (10.3s load, 98MB, 14ms vs bge 13.5s/113MB/69ms, 1.0 vs 0.6 top-1)
    embedding_dim: int = 384
    vector_store_provider: str = Field(default="chroma")  # chroma | sqlite-vec (alt)
    vector_store_collection: str = Field(default="chunks")

    # Retrieval (Phase 3)
    retrieval_top_k: int = Field(default=6, ge=1, le=20)
    retrieval_threshold: float = Field(default=0.0, ge=0.0, le=1.0)  # cosine similarity threshold (0.0 = no filtering)
    retrieval_min_score: float = Field(default=0.0)

    # LLM (Phase 4) — local-only, swappable via env
    llm_provider: str = Field(default="mock")  # mock | ollama | llamacpp
    llm_model: str = Field(default="qwen2.5:1.5b-instruct-q4_K_M")  # placeholder, not downloaded in Phase 4
    llm_host: str = Field(default="http://localhost:11434")
    llm_timeout_s: int = Field(default=30, ge=5, le=300)
    llm_max_tokens: int = Field(default=512, ge=32, le=4096)
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    allow_cloud_adapter: bool = Field(default=False)


# Singleton accessor used by pipeline/tools — import `get_config()` not raw env.
_config: IngestionConfig | None = None


def get_config() -> IngestionConfig:
    global _config
    if _config is None:
        _config = IngestionConfig()
    return _config


def reset_config() -> None:
    """For tests — reset cached singleton."""
    global _config
    _config = None
