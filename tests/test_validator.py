import tempfile
from pathlib import Path
from backend.app.ingestion.validator import validate_file, compute_sha256
from backend.app.config import reset_config


def test_allowlist_accept_all_six(tmp_path: Path):
    for name, content in [
        ("a.pdf", b"%PDF-1.4 hi"),
        ("b.csv", b"a,b\n1,2\n"),
        ("c.json", b'{"a":1}'),
        ("d.txt", b"hello"),
        ("e.log", b"2026 log line"),
        ("f.md", b"# Title"),
    ]:
        p = tmp_path / name
        p.write_bytes(content)
        r = validate_file(p, allowed_root=tmp_path)
        assert r.ok, f"{name} should be accepted: {r.error_code} {r.message}"
        assert r.file_type == name.split(".")[1]


def test_allowlist_reject_exe(tmp_path: Path):
    p = tmp_path / "bad.exe"
    p.write_bytes(b"not allowed")
    r = validate_file(p, allowed_root=tmp_path)
    assert not r.ok
    assert r.error_code == "ALLOWLIST_REJECT"


def test_magic_mismatch_pdf(tmp_path: Path):
    p = tmp_path / "fake.pdf"
    p.write_bytes(b"not a pdf")
    r = validate_file(p, allowed_root=tmp_path)
    assert not r.ok
    assert r.error_code == "MAGIC_MISMATCH"


def test_magic_ok_pdf(tmp_path: Path):
    p = tmp_path / "real.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    r = validate_file(p, allowed_root=tmp_path)
    assert r.ok


def test_traversal_reject(tmp_path: Path):
    p = tmp_path / ".." / "evil.txt"
    r = validate_file(p, allowed_root=tmp_path)
    assert not r.ok
    assert r.error_code == "PATH_TRAVERSAL"


def test_size_exceeded(tmp_path: Path):
    from backend.app.config import get_config

    cfg = get_config()
    old = cfg.max_file_size_bytes
    cfg.max_file_size_bytes = 5
    try:
        p = tmp_path / "small.txt"
        p.write_bytes(b"1234567890")
        r = validate_file(p, allowed_root=tmp_path)
        assert not r.ok
        assert r.error_code == "SIZE_EXCEEDED"
    finally:
        cfg.max_file_size_bytes = old


def test_sha256_deterministic(tmp_path: Path):
    p = tmp_path / "a.txt"
    p.write_bytes(b"hello")
    r1 = compute_sha256(p)
    r2 = compute_sha256(p)
    assert r1 == r2
    assert len(r1) == 64


def test_missing_extension(tmp_path: Path):
    p = tmp_path / "noext"
    p.write_bytes(b"hello")
    r = validate_file(p, allowed_root=tmp_path)
    assert not r.ok
    assert r.error_code == "ALLOWLIST_REJECT"
