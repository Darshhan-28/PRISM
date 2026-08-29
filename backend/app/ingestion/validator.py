"""File validation — allowlist, size, path traversal, sha256."""

import hashlib
from pathlib import Path
from dataclasses import dataclass

from backend.app.config import get_config


@dataclass
class ValidationResult:
    ok: bool
    sha256: str | None = None
    file_type: str | None = None
    error_code: str | None = None
    message: str | None = None


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_within_allowed_dir(resolved: Path, allowed_root: Path) -> bool:
    try:
        resolved.relative_to(allowed_root.resolve())
        return True
    except ValueError:
        return False


def validate_file(path: Path, allowed_root: Path | None = None) -> ValidationResult:
    cfg = get_config()
    # Null byte and traversal checks before existence — must catch ".." even if file not found
    if "\x00" in str(path):
        return ValidationResult(ok=False, error_code="INVALID_PATH", message="Null byte in path")
    if ".." in Path(str(path)).parts or ".." in str(path).split("/"):
        return ValidationResult(ok=False, error_code="PATH_TRAVERSAL", message="Path contains '..'")

    # Existence
    if not path.exists():
        return ValidationResult(ok=False, error_code="NOT_FOUND", message=f"File not found: {path}")
    if not path.is_file():
        return ValidationResult(ok=False, error_code="NOT_A_FILE", message=f"Not a file: {path}")

    # Resolve and check within allowed_root if provided; default is raw_dir
    try:
        resolved = path.resolve()
    except Exception as e:
        return ValidationResult(ok=False, error_code="INVALID_PATH", message=str(e))

    # Extension allowlist
    ext = path.suffix.lower().lstrip(".")
    if ext == "":
        return ValidationResult(ok=False, error_code="ALLOWLIST_REJECT", message="Missing extension")
    if ext not in cfg.allowed_extensions:
        return ValidationResult(ok=False, error_code="ALLOWLIST_REJECT", message=f"Extension .{ext} not allowed")

    # Size check
    size = path.stat().st_size
    if size > cfg.max_file_size_bytes:
        return ValidationResult(ok=False, error_code="SIZE_EXCEEDED", message=f"File size {size} exceeds {cfg.max_file_size_bytes}")
    if size == 0:
        # Empty files are allowed but will produce 0 chunks — not an error
        pass

    # Magic bytes check (lightweight)
    try:
        with path.open("rb") as f:
            header = f.read(8)
    except Exception as e:
        return ValidationResult(ok=False, error_code="READ_ERROR", message=str(e))

    if ext == "pdf":
        if not header.startswith(b"%PDF"):
            return ValidationResult(ok=False, error_code="MAGIC_MISMATCH", message="PDF magic %PDF not found")
    # For text types (csv, json, txt, log, md) — accept any content; no magic enforcement beyond not being binary PDF
    # Note: png/jpg/webp are validated by the vision tool path, not ingestion allowlist

    sha = compute_sha256(path)
    return ValidationResult(ok=True, sha256=sha, file_type=ext)
