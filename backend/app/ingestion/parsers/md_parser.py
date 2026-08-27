"""MD parser — heading-aware section split, preserves verbatim text."""

from pathlib import Path
from datetime import datetime
import re

from backend.app.ingestion.models import NormalizedDocument, Page

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")


class MdParser:
    def parse(self, path: Path, doc_id: str, sha256: str, ingested_at: datetime) -> NormalizedDocument:
        for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
            try:
                raw = path.read_text(encoding=enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raw = path.read_bytes().decode("utf-8", errors="replace")

        lines = raw.splitlines()
        # Extract sections by headings
        sections: list[tuple[str | None, list[str]]] = []
        current_heading: str | None = None
        current_lines: list[str] = []

        for line in lines:
            m = HEADING_RE.match(line)
            if m:
                # flush previous
                if current_lines or current_heading is not None:
                    sections.append((current_heading, current_lines))
                elif current_lines:
                    sections.append((None, current_lines))
                current_heading = m.group(2).strip()
                current_lines = [line]
            else:
                current_lines.append(line)
        # flush last
        sections.append((current_heading, current_lines))

        # If no headings, single page
        if len(sections) == 1 and sections[0][0] is None:
            text = raw
            pages = [Page(page_number=1, text=text, char_count=len(text), section=None)]
        else:
            pages = []
            for idx, (heading, seg_lines) in enumerate(sections, start=1):
                text = "\n".join(seg_lines).strip()
                if not text:
                    continue
                pages.append(Page(page_number=idx, text=text, char_count=len(text), section=heading))

        if not pages:
            pages = [Page(page_number=1, text=raw, char_count=len(raw))]

        # Title = first H1 or filename
        title = path.stem
        for h, _ in sections:
            if h:
                title = h
                break
        # Prefer first H1 if exists
        for line in lines:
            m = HEADING_RE.match(line)
            if m and len(m.group(1)) == 1:
                title = m.group(2).strip()
                break

        return NormalizedDocument(
            document_id=doc_id,
            filename=path.name,
            file_type="md",
            source_path=str(path.resolve()),
            sha256=sha256,
            title=title,
            ingested_at=ingested_at,
            pages=pages,
            structured_rows=None,
            warnings=[],
        )
