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
    embedding_model: str = Field(default="BAAI/bge-small-en-v1.5")
    embedding_dim: int = 384
    vector_store_provider: str = Field(default="chroma")  # chroma | sqlite-vec (alt)
    vector_store_collection: str = Field(default="chunks")


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
