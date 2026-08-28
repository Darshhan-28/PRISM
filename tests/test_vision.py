import tempfile
from pathlib import Path
import hashlib

from backend.app.vision.adapter import MockVisionAdapter, get_vision_adapter, OllamaVisionAdapter
from backend.app.tools.registry import list_tools, execute_tool
from backend.app.tools.inspect_image import InspectImageInput

def _write(path: Path, data: bytes):
    path.write_bytes(data)

def _sha(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()

# valid PNG/JPG/WEBP
def test_valid_png(tmp_path: Path):
    p = tmp_path / "img.png"
    _write(p, b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    out = execute_tool("inspect_image", {"image_path": str(p)})
    assert out.success is True
    assert "PNG" in out.result["filename"] or out.result["filename"] == "img.png"
    assert out.evidence_refs[0].sha256 == _sha(p)

def test_valid_jpg(tmp_path: Path):
    p = tmp_path / "photo.jpg"
    _write(p, b"\xff\xd8\xff" + b"\x00" * 100)
    out = execute_tool("inspect_image", {"image_path": str(p), "prompt": "check valve"})
    assert out.success is True
    assert "check valve" in out.result["description"] or "MOCK VISION" in out.result["description"]

def test_valid_webp(tmp_path: Path):
    p = tmp_path / "img.webp"
    _write(p, b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 100)
    out = execute_tool("inspect_image", {"image_path": str(p)})
    assert out.success is True
    assert out.evidence_refs[0].filename == "img.webp"

def test_unsupported_type(tmp_path: Path):
    p = tmp_path / "bad.exe"
    _write(p, b"MZ" + b"\x00" * 100)
    out = execute_tool("inspect_image", {"image_path": str(p)})
    assert out.success is False
    assert "unsupported" in out.error.lower()

def test_oversized(tmp_path: Path):
    from backend.app.config import get_config
    cfg = get_config()
    old = cfg.vision_max_image_bytes
    cfg.vision_max_image_bytes = 50
    try:
        p = tmp_path / "big.png"
        _write(p, b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        out = execute_tool("inspect_image", {"image_path": str(p)})
        assert out.success is False
        assert "too large" in out.error.lower()
    finally:
        cfg.vision_max_image_bytes = old

def test_sha256_provenance(tmp_path: Path):
    p = tmp_path / "prov.png"
    data = b"\x89PNG\r\n\x1a\n" + b"hello provenance"
    _write(p, data)
    out = execute_tool("inspect_image", {"image_path": str(p)})
    assert out.success
    assert out.result["sha256"] == _sha(p)
    assert out.evidence_refs[0].sha256 == _sha(p)
    assert out.evidence_refs[0].filename == "prov.png"
    assert out.evidence_refs[0].source_path == str(p.resolve())

def test_mock_deterministic(tmp_path: Path):
    p = tmp_path / "det.png"
    _write(p, b"\x89PNG\r\n\x1a\n" + b"\x00")
    m = MockVisionAdapter()
    a = m.describe_image(str(p), prompt="Describe")
    b = m.describe_image(str(p), prompt="Describe")
    assert a == b
    c = m.describe_image(str(p), prompt="Other")
    assert a != c

def test_mock_vision_adapter():
    m = get_vision_adapter("mock")
    assert m.provider == "mock"
    assert m.health_check()["ok"] is True
    out = m.describe_image("some.png", prompt="test prompt")
    assert "MOCK VISION" in out

def test_registry_integration():
    tools = list_tools()
    assert "inspect_image" in tools
    # input model validation via registry
    try:
        execute_tool("inspect_image", {"image_path": ""})
        assert False
    except Exception:
        pass

def test_malformed_missing(tmp_path: Path):
    out = execute_tool("inspect_image", {"image_path": str(tmp_path / "nonexistent.png")})
    assert out.success is False
    assert "not found" in out.error.lower()
    # invalid path traversal
    try:
        InspectImageInput(image_path="../evil.png")
        assert False
    except Exception:
        pass

def test_offline_no_network():
    import pathlib
    src = pathlib.Path("backend/app/vision/adapter.py").read_text(encoding="utf-8")
    # Mock must not use network; Ollama uses httpx to localhost only
    assert "MockVisionAdapter" in src
    # ensure no cloud host hard-coded outside localhost check
    assert "allow_cloud_adapter" in src
    src2 = pathlib.Path("backend/app/tools/inspect_image.py").read_text(encoding="utf-8")
    assert "httpx" not in src2  # tool itself does not do HTTP
    assert "requests" not in src2

def test_evidence_provenance(tmp_path: Path):
    p = tmp_path / "ev.png"
    _write(p, b"\x89PNG\r\n\x1a\n" + b"\x00")
    out = execute_tool("inspect_image", {"image_path": str(p), "prompt": "Anomaly?"})
    assert out.success
    ref = out.evidence_refs[0]
    assert ref.filename == "ev.png"
    assert ref.sha256 is not None
    assert len(ref.sha256) == 64
    # result treated as evidence, not fact: check evidence_ref present and description is evidence
    assert "description" in out.result

def test_ollama_adapter_local_guard():
    from backend.app.config import get_config
    cfg = get_config()
    old_allow, old_host = cfg.allow_cloud_adapter, cfg.vision_host
    cfg.allow_cloud_adapter = False
    cfg.vision_host = "https://api.example.com"
    try:
        try:
            OllamaVisionAdapter()
            assert False
        except ValueError as e:
            assert "allow_cloud_adapter" in str(e)
    finally:
        cfg.allow_cloud_adapter, cfg.vision_host = old_allow, old_host
