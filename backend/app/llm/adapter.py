"""LLMAdapter protocol + shared types and errors. No cloud, no model import."""

from dataclasses import dataclass
from typing import Protocol, AsyncIterator


class LLMError(RuntimeError):
    """Base LLM error."""


class LLMTimeoutError(LLMError):
    """LLM request timed out."""


class LLMUnavailableError(LLMError):
    """Model/daemon unavailable (e.g., Ollama not running, model not pulled)."""


@dataclass
class GenerateParams:
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_s: int | None = None
    stop: list[str] | None = None


def validate_prompt(prompt: str) -> None:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be non-empty string")
    if len(prompt) > 8000:
        raise ValueError("prompt too long (max 8000 chars)")


def validate_system(system: str | None) -> None:
    if system is None:
        return
    if not isinstance(system, str):
        raise ValueError("system must be string or None")
    if len(system) > 2000:
        raise ValueError("system too long (max 2000 chars)")


class LLMAdapter(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    def generate(self, prompt: str, system: str | None = None, **kwargs) -> str: ...

    async def generate_stream(self, prompt: str, system: str | None = None, **kwargs) -> AsyncIterator[str]: ...

    def health_check(self) -> dict: ...
