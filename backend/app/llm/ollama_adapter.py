"""OllamaAdapter — local HTTP to Ollama daemon. No cloud, no fallback."""

import time
from typing import AsyncIterator

import httpx

from backend.app.config import get_config
from backend.app.llm.adapter import LLMError, LLMTimeoutError, LLMUnavailableError, validate_prompt, validate_system


def _is_local_host(host: str) -> bool:
    h = host.lower()
    return "localhost" in h or "127.0.0.1" in h or host.startswith("http://localhost") or host.startswith("http://127.0.0.1")


class OllamaAdapter:
    provider: str = "ollama"

    def __init__(self, host: str | None = None, model: str | None = None, timeout_s: int | None = None):
        cfg = get_config()
        self.host = (host or cfg.llm_host).rstrip("/")
        self.model = model or cfg.llm_model
        self.timeout_s = timeout_s or cfg.llm_timeout_s

        # Offline guard: block non-local host when cloud disabled
        if not cfg.allow_cloud_adapter and not _is_local_host(self.host):
            raise ValueError(f"allow_cloud_adapter=false blocks non-local host: {self.host}")

        self._client = httpx.Client(
            base_url=self.host,
            timeout=httpx.Timeout(float(self.timeout_s)),
            follow_redirects=False,
        )

    def _build_payload(self, prompt: str, system: str | None, kwargs: dict) -> dict:
        temperature = kwargs.get("temperature")
        max_tokens = kwargs.get("max_tokens")
        if temperature is None:
            temperature = get_config().llm_temperature
        if max_tokens is None:
            max_tokens = get_config().llm_max_tokens
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": float(temperature), "num_predict": int(max_tokens)},
        }
        if system:
            payload["system"] = system
        stop = kwargs.get("stop")
        if stop:
            payload["options"]["stop"] = stop
        return payload

    def generate(self, prompt: str, system: str | None = None, **kwargs) -> str:
        validate_prompt(prompt)
        validate_system(system)
        payload = self._build_payload(prompt, system, kwargs)
        try:
            resp = self._client.post("/api/generate", json=payload)
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(f"Ollama timeout after {self.timeout_s}s: {e}") from e
        except httpx.ConnectError as e:
            raise LLMUnavailableError(f"Ollama not reachable at {self.host} — is ollama serve running? {e}") from e
        except httpx.RequestError as e:
            raise LLMError(f"Ollama request error: {e}") from e

        if resp.status_code == 404:
            raise LLMUnavailableError(f"model {self.model} not found — ollama pull {self.model} (404)")
        if resp.status_code >= 400:
            # do not leak full body, truncate
            body = resp.text[:500]
            raise LLMError(f"Ollama error {resp.status_code}: {body}")

        try:
            data = resp.json()
        except Exception as e:
            raise LLMError(f"Ollama invalid JSON: {e}") from e

        text = data.get("response") or data.get("content") or ""
        if not isinstance(text, str):
            text = str(text)
        return text

    async def generate_stream(self, prompt: str, system: str | None = None, **kwargs) -> AsyncIterator[str]:
        # Phase 4 stub: yield result of generate() in 2 chunks (no async HTTP yet)
        text = self.generate(prompt, system, **kwargs)
        mid = max(1, len(text) // 2)
        yield text[:mid]
        yield text[mid:]

    def health_check(self) -> dict:
        start = time.time()
        try:
            resp = self._client.get("/api/tags", timeout=httpx.Timeout(5.0))
            ok = resp.status_code == 200
            latency = int((time.time() - start) * 1000)
            if ok:
                return {"ok": True, "provider": self.provider, "model": self.model, "latency_ms": latency, "error": None}
            return {"ok": False, "provider": self.provider, "model": self.model, "latency_ms": latency, "error": f"status {resp.status_code}"}
        except Exception as e:
            latency = int((time.time() - start) * 1000)
            return {"ok": False, "provider": self.provider, "model": self.model, "latency_ms": latency, "error": str(e)[:300]}

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
