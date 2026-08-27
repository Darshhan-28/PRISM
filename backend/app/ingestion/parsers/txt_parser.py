"""TXT and LOG parser — line-preserving read with encoding cascade."""

from pathlib import Path
from datetime import datetime

from backend.app.ingestion.models import NormalizedDocument, Page


def _read_text_with_fallback(path: Path) -> tuple[str, str]:
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=enc), enc
        except UnicodeDecodeError:
            continue
        except Exception:
            continue
    # last resort
    return path.read_bytes().decode("utf-8", errors="replace"), "utf-8-replace"


class TxtParser:
    """Handles both .txt and .log — identical logic, file_type set from path suffix."""

    def parse(self, path: Path, doc_id: str, sha256: str, ingested_at: datetime) -> NormalizedDocument:
        text, enc = _read_text_with_fallback(path)
        warnings: list[str] = []
        if enc in ("cp1252", "latin-1", "utf-8-replace"):
            warnings.append(f"encoding fallback: {enc}")

        # For LOG, we keep raw lines; no structured extraction yet (deferred to tools)
        file_type = path.suffix.lower().lstrip(".")
        if file_type not in ("txt", "log"):
            file_type = "txt"

        page = Page(page_number=1, text=text, char_count=len(text))
        title = path.stem
        return NormalizedDocument(
            document_id=doc_id,
            filename=path.name,
            file_type=file_type,  # type: ignore[arg-type]
            source_path=str(path.resolve()),
            sha256=sha256,
            title=title,
            ingested_at=ingested_at,
            pages=[page],
            structured_rows=None,
            warnings=warnings,
        )
