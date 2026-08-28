"""MockAdapter — deterministic, offline, no network, for tests and default."""

import time
from typing import AsyncIterator

from backend.app.llm.adapter import validate_prompt, validate_system


class MockAdapter:
    provider: str = "mock"
    model: str = "mock-1"

    def __init__(self, canned: dict[str, str] | None = None):
        self.canned = canned or {}

    def generate(self, prompt: str, system: str | None = None, **kwargs) -> str:
        validate_prompt(prompt)
        validate_system(system)
        # canned substring match (first hit)
        for key, val in self.canned.items():
            if key in prompt:
                return val
        # heuristic for demo: if prompt mentions SOP/pressure
        if "SOP" in prompt or "pressure" in prompt.lower():
            return "According to SOP P-204, normal pressure is 2.1-3.4 bar. [MOCK, evidence: SOP_P-204.pdf]"
        return f"[MOCK] echo: {prompt[:120]}"

    async def generate_stream(self, prompt: str, system: str | None = None, **kwargs) -> AsyncIterator[str]:
        text = self.generate(prompt, system, **kwargs)
        # yield in 2 chunks to simulate streaming without delay
        mid = max(1, len(text) // 2)
        yield text[:mid]
        yield text[mid:]

    def health_check(self) -> dict:
        return {"ok": True, "provider": self.provider, "model": self.model, "latency_ms": 0, "error": None}
