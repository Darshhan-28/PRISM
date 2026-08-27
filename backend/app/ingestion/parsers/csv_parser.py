"""CSV parser — stdlib csv with dialect sniffing."""

from pathlib import Path
from datetime import datetime
import csv

from backend.app.ingestion.models import NormalizedDocument, Page


def _detect_encoding(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            with path.open("r", encoding=enc) as f:
                f.read(2048)
            return enc
        except UnicodeDecodeError:
            continue
    return "utf-8"


class CsvParser:
    def parse(self, path: Path, doc_id: str, sha256: str, ingested_at: datetime) -> NormalizedDocument:
        enc = _detect_encoding(path)
        try:
            with path.open("r", encoding=enc, newline="") as f:
                sample = f.read(4096)
                f.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                except Exception:
                    dialect = csv.excel
                reader = csv.DictReader(f, dialect=dialect)
                if reader.fieldnames is None:
                    raise ValueError("CSV_MALFORMED: no header")
                rows = [dict(r) for r in reader]
                fieldnames = reader.fieldnames
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"CSV_MALFORMED: {e}") from e

        # Text representation for chunking
        if rows:
            header_line = ", ".join(fieldnames or [])
            row_texts = []
            for r in rows:
                row_texts.append(", ".join(f"{k}: {v}" for k, v in r.items()))
            full_text = header_line + "\n" + "\n".join(row_texts)
        else:
            full_text = ", ".join(fieldnames or [])

        page = Page(page_number=1, text=full_text, char_count=len(full_text))
        return NormalizedDocument(
            document_id=doc_id,
            filename=path.name,
            file_type="csv",
            source_path=str(path.resolve()),
            sha256=sha256,
            title=path.stem,
            ingested_at=ingested_at,
            pages=[page],
            structured_rows=rows,
            warnings=[],
        )
