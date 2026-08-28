from backend.app.config import get_config, reset_config
from backend.app.llm.factory import get_llm_adapter


def test_factory_mock_default():
    cfg = get_config()
    old = cfg.llm_provider
    cfg.llm_provider = "mock"
    try:
        a = get_llm_adapter()
        assert a.provider == "mock"
    finally:
        cfg.llm_provider = old


def test_factory_explicit_mock():
    a = get_llm_adapter("mock")
    assert a.provider == "mock"


def test_factory_unknown():
    try:
        get_llm_adapter("unknown_xyz")
        assert False
    except ValueError as e:
        assert "Unknown" in str(e)


def test_factory_ollama_returns_adapter():
    cfg = get_config()
    old_p, old_h = cfg.llm_provider, cfg.llm_host
    cfg.llm_provider = "ollama"
    cfg.llm_host = "http://localhost:11434"
    try:
        a = get_llm_adapter("ollama")
        assert a.provider == "ollama"
        assert "localhost" in a.host
        a.close()
    finally:
        cfg.llm_provider, cfg.llm_host = old_p, old_h


def test_factory_cloud_blocked():
    cfg = get_config()
    old_allow, old_host = cfg.allow_cloud_adapter, cfg.llm_host
    cfg.allow_cloud_adapter = False
    cfg.llm_host = "https://api.openai.com"
    try:
        try:
            get_llm_adapter("ollama")
            assert False, "should block non-local host"
        except ValueError as e:
            assert "allow_cloud_adapter" in str(e)
    finally:
        cfg.allow_cloud_adapter, cfg.llm_host = old_allow, old_host


def test_factory_cloud_allowed_when_flag_true():
    cfg = get_config()
    old_allow, old_host = cfg.allow_cloud_adapter, cfg.llm_host
    cfg.allow_cloud_adapter = True
    cfg.llm_host = "https://api.example.com"
    try:
        a = get_llm_adapter("ollama")
        assert a.host == "https://api.example.com"
        a.close()
    finally:
        cfg.allow_cloud_adapter, cfg.llm_host = old_allow, old_host
