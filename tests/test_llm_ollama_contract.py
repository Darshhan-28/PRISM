"""Ollama contract tests — mocked HTTP, no daemon required."""
import httpx
from backend.app.config import get_config
from backend.app.llm.ollama_adapter import OllamaAdapter
from backend.app.llm.adapter import LLMTimeoutError, LLMUnavailableError, LLMError


def _make_adapter():
    cfg = get_config()
    old = (cfg.llm_host, cfg.llm_model, cfg.llm_timeout_s)
    cfg.llm_host = "http://localhost:11434"
    cfg.llm_model = "qwen2.5:1.5b-instruct-q4_K_M"
    cfg.llm_timeout_s = 5
    a = OllamaAdapter()
    return a, old


def _restore(old):
    cfg = get_config()
    cfg.llm_host, cfg.llm_model, cfg.llm_timeout_s = old


def test_generate_success_mocked():
    a, old = _make_adapter()
    try:
        # Use httpx MockTransport to fake Ollama
        def handler(request: httpx.Request):
            assert request.url.path == "/api/generate"
            return httpx.Response(200, json={"response": "hello from ollama"})

        transport = httpx.MockTransport(handler)
        a._client = httpx.Client(base_url=a.host, transport=transport)
        out = a.generate("hi")
        assert out == "hello from ollama"
    finally:
        a.close()
        _restore(old)


def test_generate_404_model_not_found():
    a, old = _make_adapter()
    try:
        def handler(request: httpx.Request):
            return httpx.Response(404, text="model not found")

        a._client = httpx.Client(base_url=a.host, transport=httpx.MockTransport(handler))
        try:
            a.generate("hi")
            assert False
        except LLMUnavailableError as e:
            assert "not found" in str(e).lower()
    finally:
        a.close()
        _restore(old)


def test_generate_timeout():
    a, old = _make_adapter()
    try:
        def handler(request: httpx.Request):
            raise httpx.TimeoutException("timeout", request=request)

        a._client = httpx.Client(base_url=a.host, transport=httpx.MockTransport(handler))
        try:
            a.generate("hi")
            assert False
        except LLMTimeoutError:
            pass
    finally:
        a.close()
        _restore(old)


def test_generate_connect_error():
    a, old = _make_adapter()
    try:
        def handler(request: httpx.Request):
            raise httpx.ConnectError("refused", request=request)

        a._client = httpx.Client(base_url=a.host, transport=httpx.MockTransport(handler))
        try:
            a.generate("hi")
            assert False
        except LLMUnavailableError as e:
            assert "not reachable" in str(e).lower()
    finally:
        a.close()
        _restore(old)


def test_health_check_success():
    a, old = _make_adapter()
    try:
        def handler(request: httpx.Request):
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": []})
            return httpx.Response(404, text="")

        a._client = httpx.Client(base_url=a.host, transport=httpx.MockTransport(handler))
        h = a.health_check()
        assert h["ok"] is True
        assert h["latency_ms"] >= 0
    finally:
        a.close()
        _restore(old)


def test_health_check_failure():
    a, old = _make_adapter()
    try:
        def handler(request: httpx.Request):
            raise httpx.ConnectError("down", request=request)

        a._client = httpx.Client(base_url=a.host, transport=httpx.MockTransport(handler))
        h = a.health_check()
        assert h["ok"] is False
        assert h["error"] is not None
    finally:
        a.close()
        _restore(old)
