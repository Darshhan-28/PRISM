"""PDF parser using pypdf — per-page text extraction."""

from pathlib import Path
from datetime import datetime, timezone
from pypdf import PdfReader

from backend.app.ingestion.models import NormalizedDocument, Page


def _pdf_title(reader: PdfReader, fallback: str) -> str:
    try:
        meta = reader.metadata
        if meta and meta.title:
            t = str(meta.title).strip()
            if t:
                return t
    except Exception:
        pass
    return fallback


class PdfParser:
    def parse(self, path: Path, doc_id: str, sha256: str, ingested_at: datetime) -> NormalizedDocument:
        try:
            reader = PdfReader(str(path))
        except Exception as e:
            raise ValueError(f"PDF_MALFORMED: {e}") from e

        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise ValueError("PDF_ENCRYPTED")

        pages: list[Page] = []
        warnings: list[str] = []
        for i, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
            except Exception as e:
                text = ""
                warnings.append(f"page {i+1} extract failed: {e}")
            text = text.strip()
            is_scanned = False
            if not text:
                text = f"[non-extractable page {i+1} — scanned image, OCR deferred to Phase 10]"
                is_scanned = True
                warnings.append(f"page {i+1} non-extractable")
            pages.append(Page(page_number=i + 1, text=text, char_count=len(text), is_scanned=is_scanned))

        if not pages:
            pages = [Page(page_number=1, text="[empty PDF]", char_count=11)]
            warnings.append("empty PDF")

        title = _pdf_title(reader, path.stem)
        return NormalizedDocument(
            document_id=doc_id,
            filename=path.name,
            file_type="pdf",
            source_path=str(path.resolve()),
            sha256=sha256,
            title=title,
            ingested_at=ingested_at,
            pages=pages,
            structured_rows=None,
            warnings=warnings,
        )
