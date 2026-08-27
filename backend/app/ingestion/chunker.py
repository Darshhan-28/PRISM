"""Deterministic overlapping chunker — no tokenizer, no LLM."""

from datetime import datetime

from backend.app.config import get_config
from backend.app.ingestion.models import NormalizedDocument, Chunk, ChunkMetadata


def _find_break(text: str, start: int, end: int) -> int:
    """Prefer break at paragraph or sentence boundary in last 20% of window."""
    if end >= len(text):
        return len(text)
    window_len = end - start
    search_start = end - int(window_len * 0.2)
    # prefer double newline
    idx = text.rfind("\n\n", search_start, end)
    if idx != -1:
        return idx + 2
    # then single newline
    idx = text.rfind("\n", search_start, end)
    if idx != -1:
        return idx + 1
    # then sentence boundary ". "
    idx = text.rfind(". ", search_start, end)
    if idx != -1:
        return idx + 2
    return end


def chunk_document(doc: NormalizedDocument, chunk_size: int | None = None, chunk_overlap: int | None = None) -> list[Chunk]:
    cfg = get_config()
    cs = chunk_size or cfg.chunk_size
    co = chunk_overlap or cfg.chunk_overlap
    if co >= cs:
        raise ValueError("chunk_overlap must be < chunk_size")

    # Build full text with page markers and map positions to page/line info
    # For simplicity we concatenate pages with "\n\n" and track offsets.
    # Line ranges are best-effort for TXT/LOG/MD (single page).
    full_text_parts: list[str] = []
    page_offsets: list[tuple[int, int, int, str | None]] = []  # (start, end, page_number, section)
    offset = 0
    for pg in doc.pages:
        txt = pg.text
        if full_text_parts:
            full_text_parts.append("\n\n")
            offset += 2
        start = offset
        full_text_parts.append(txt)
        end = start + len(txt)
        page_offsets.append((start, end, pg.page_number, pg.section))
        offset = end

    full_text = "".join(full_text_parts)
    if not full_text.strip():
        return []

    # Precompute line starts for line_range (global)
    line_starts: list[int] = [0]
    for i, ch in enumerate(full_text):
        if ch == "\n":
            line_starts.append(i + 1)

    def offset_to_line(off: int) -> int:
        # 1-indexed line number
        import bisect
        return bisect.bisect_right(line_starts, off)

    chunks: list[Chunk] = []
    idx = 0
    chunk_index = 0
    n = len(full_text)
    while idx < n:
        end = min(idx + cs, n)
        # avoid breaking mid-window unless at end
        if end < n:
            end = _find_break(full_text, idx, end)
            # ensure forward progress
            if end <= idx:
                end = min(idx + cs, n)
        text = full_text[idx:end].strip()
        if text:
            # Determine page_number: page that contains majority of chunk
            # Find page where chunk midpoint lies
            mid = idx + len(text) // 2
            page_num: int | None = None
            section: str | None = None
            for s, e, pn, sec in page_offsets:
                if s <= mid < e:
                    page_num = pn
                    section = sec
                    break
            if page_num is None and page_offsets:
                page_num = page_offsets[0][2]
                section = page_offsets[0][3]

            # If chunk spans multiple pages, set page_number to None per model spec? 
            # Keep originating page as page_num for traceability; spec allows None if spans.
            # We keep page_num as found midpoint page.
            line_start = offset_to_line(idx)
            line_end = offset_to_line(end - 1) if end > idx else line_start

            token_est = max(1, len(text) // 4)
            chunk_id = f"{doc.document_id}:p{page_num or 1}:c{chunk_index:04d}"
            meta = ChunkMetadata(
                document_id=doc.document_id,
                filename=doc.filename,
                file_type=doc.file_type,
                source_path=doc.source_path,
                sha256=doc.sha256,
                chunk_id=chunk_id,
                page_number=page_num,
                line_range=(line_start, line_end),
                section=section,
                title=doc.title,
                ingested_at=doc.ingested_at,
                chunk_index=chunk_index,
                token_estimate=token_est,
                char_count=len(text),
            )
            chunks.append(Chunk(chunk_id=chunk_id, document_id=doc.document_id, text=text, metadata=meta))
            chunk_index += 1

        if end >= n:
            break
        idx = end - co
        if idx < 0:
            idx = 0

    return chunks
