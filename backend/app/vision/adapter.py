"""VisionAdapter — provider-agnostic local vision, mock default, offline."""

import base64
import hashlib
from pathlib import Path
from typing import Protocol

import httpx

from backend.app.config import get_config


class VisionError(RuntimeError):
    pass


class VisionUnavailableError(VisionError):
    pass


class VisionAdapter(Protocol):
    def describe_image(self, image_path: str | Path, prompt: str | None = None) -> str: ...

    def health_check(self) -> dict: ...


def _is_local_host(host: str) -> bool:
    h = host.lower()
    return "localhost" in h or "127.0.0.1" in h


class MockVisionAdapter:
    provider: str = "mock"
    model: str = "mock-vision-1"

    def describe_image(self, image_path: str | Path, prompt: str | None = None) -> str:
        p = Path(image_path)
        prompt = prompt or "Describe equipment condition and visible anomalies."
        # Deterministic, offline, no bytes read beyond filename
        return f"[MOCK VISION] {p.name}: {prompt} -> No anomalies detected (mock)."

    def health_check(self) -> dict:
        return {"ok": True, "provider": self.provider, "model": self.model}


class OllamaVisionAdapter:
    provider: str = "ollama"

    def __init__(self, host: str | None = None, model: str | None = None, timeout_s: int | None = None):
        cfg = get_config()
        self.host = (host or cfg.vision_host).rstrip("/")
        self.model = model or cfg.vision_model
        self.timeout_s = timeout_s or cfg.vision_timeout_s
        if not cfg.allow_cloud_adapter and not _is_local_host(self.host):
            raise ValueError(f"allow_cloud_adapter=false blocks non-local host: {self.host}")
        self._client = httpx.Client(base_url=self.host, timeout=httpx.Timeout(float(self.timeout_s)), follow_redirects=False)

    def describe_image(self, image_path: str | Path, prompt: str | None = None) -> str:
        p = Path(image_path)
        if not p.exists():
            raise VisionUnavailableError(f"image not found: {p}")
        prompt = prompt or "Describe equipment condition and visible anomalies."
        # Local only: read bytes and send as base64 to Ollama
        data = p.read_bytes()
        b64 = base64.b64encode(data).decode("utf-8")
        payload = {"model": self.model, "prompt": prompt, "stream": False, "images": [b64]}
        try:
            resp = self._client.post("/api/generate", json=payload)
        except httpx.TimeoutException as e:
            raise VisionError(f"Ollama vision timeout after {self.timeout_s}s: {e}") from e
        except httpx.ConnectError as e:
            raise VisionUnavailableError(f"Ollama not reachable at {self.host}: {e}") from e
        if resp.status_code == 404:
            raise VisionUnavailableError(f"model {self.model} not found")
        if resp.status_code >= 400:
            raise VisionError(f"Ollama vision error {resp.status_code}: {resp.text[:500]}")
        try:
            j = resp.json()
        except Exception as e:
            raise VisionError(f"Invalid JSON: {e}") from e
        return str(j.get("response") or j.get("content") or "")

    def health_check(self) -> dict:
        import time

        start = time.time()
        try:
            r = self._client.get("/api/tags", timeout=httpx.Timeout(5.0))
            ok = r.status_code == 200
            return {"ok": ok, "provider": self.provider, "model": self.model, "latency_ms": int((time.time() - start) * 1000), "error": None if ok else f"status {r.status_code}"}
        except Exception as e:
            return {"ok": False, "provider": self.provider, "model": self.model, "latency_ms": int((time.time() - start) * 1000), "error": str(e)[:300]}

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass


def get_vision_adapter(provider: str | None = None):
    cfg = get_config()
    p = (provider or cfg.vision_provider).lower()
    if p == "mock":
        return MockVisionAdapter()
    if p == "ollama":
        return OllamaVisionAdapter()
    raise ValueError(f"Unknown vision_provider: {p}. Allowed: mock, ollama")
