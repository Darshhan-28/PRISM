"""Factory — get_llm_adapter() swappable via config/env, no code change."""

from backend.app.config import get_config
from backend.app.llm.mock_adapter import MockAdapter
from backend.app.llm.ollama_adapter import OllamaAdapter


def get_llm_adapter(provider: str | None = None):
    cfg = get_config()
    p = (provider or cfg.llm_provider).lower()
    if p == "mock":
        return MockAdapter()
    if p == "ollama":
        return OllamaAdapter()
    if p == "llamacpp":
        raise NotImplementedError("llamacpp adapter deferred — Phase 4 mock/ollama only")
    raise ValueError(f"Unknown llm_provider: {p}. Allowed: mock, ollama")
