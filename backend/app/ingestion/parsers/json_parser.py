"""JSON parser — stdlib json, handles array or object, stores structured rows."""

from pathlib import Path
from datetime import datetime
import json

from backend.app.ingestion.models import NormalizedDocument, Page


class JsonParser:
    def parse(self, path: Path, doc_id: str, sha256: str, ingested_at: datetime) -> NormalizedDocument:
        enc = "utf-8"
        raw = None
        for trial in ("utf-8", "utf-8-sig", "cp1252"):
            try:
                raw = path.read_text(encoding=trial)
                enc = trial
                break
            except UnicodeDecodeError:
                continue
        if raw is None:
            raw = path.read_text(encoding="latin-1")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON_INVALID: {e}") from e

        if isinstance(data, list):
            rows = data
            pretty = json.dumps(data, indent=2, ensure_ascii=False)
        elif isinstance(data, dict):
            rows = [data]
            pretty = json.dumps(data, indent=2, ensure_ascii=False)
        else:
            rows = [{"value": data}]
            pretty = json.dumps(data, indent=2, ensure_ascii=False)

        # Also build per-row text
        row_texts = [json.dumps(r, ensure_ascii=False) for r in rows]
        combined = pretty  # full pretty for page text; row_texts available via structured_rows

        page = Page(page_number=1, text=combined, char_count=len(combined))
        return NormalizedDocument(
            document_id=doc_id,
            filename=path.name,
            file_type="json",
            source_path=str(path.resolve()),
            sha256=sha256,
            title=path.stem,
            ingested_at=ingested_at,
            pages=[page],
            structured_rows=rows,
            warnings=[],
        )
