"""Ingestion pipeline — validate → parse → chunk → persist (SQLite + processed JSON)."""

import uuid
import json
import time
from pathlib import Path
from datetime import datetime, timezone

from backend.app.config import get_config
from backend.app.ingestion.validator import validate_file, ValidationResult
from backend.app.ingestion.models import IngestResult, IngestionReport, NormalizedDocument
from backend.app.ingestion.chunker import chunk_document
from backend.app.store.db import get_connection, init_db

# Parsers
from backend.app.ingestion.parsers.pdf_parser import PdfParser
from backend.app.ingestion.parsers.csv_parser import CsvParser
from backend.app.ingestion.parsers.json_parser import JsonParser
from backend.app.ingestion.parsers.txt_parser import TxtParser
from backend.app.ingestion.parsers.md_parser import MdParser

PARSER_MAP = {
    "pdf": PdfParser(),
    "csv": CsvParser(),
    "json": JsonParser(),
    "txt": TxtParser(),
    "log": TxtParser(),
    "md": MdParser(),
}


class IngestionPipeline:
    def __init__(self, processed_dir: Path | None = None, db_path: Path | None = None):
        cfg = get_config()
        self.processed_dir = Path(processed_dir or cfg.processed_dir)
        self.db_path = Path(db_path or cfg.db_path)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        init_db(self.db_path)

    def ingest_files(self, paths: list[Path], embed_and_store: bool = False) -> IngestionReport:
        """Ingest list of file paths. Per-file isolation: one failure never aborts batch."""
        start = time.time()
        per_file: list[IngestResult] = []
        total_chunks = 0
        total_pages = 0
        ingested = rejected = failed = 0

        # Optional embed/vector store - lazy import to avoid heavy deps when not needed
        embedder = None
        vector_store = None
        if embed_and_store:
            from backend.app.retrieval.embedder import get_embedder
            from backend.app.retrieval.vector_store import get_vector_store

            embedder = get_embedder()
            vector_store = get_vector_store()

        for p in paths:
            result = self._ingest_single(p, embedder, vector_store)
            per_file.append(result)
            if result.status == "ingested":
                ingested += 1
                total_chunks += result.chunks or 0
                total_pages += result.pages or 0
            elif result.status == "rejected":
                rejected += 1
            else:
                failed += 1

        duration_ms = int((time.time() - start) * 1000)
        return IngestionReport(
            total_files=len(paths),
            ingested=ingested,
            rejected=rejected,
            failed=failed,
            total_chunks=total_chunks,
            total_pages=total_pages,
            duration_ms=duration_ms,
            per_file=per_file,
        )

    def ingest_directory(self, directory: Path, recursive: bool = True, embed_and_store: bool = False) -> IngestionReport:
        pattern = "**/*" if recursive else "*"
        paths = [p for p in directory.glob(pattern) if p.is_file()]
        # filter to allowed extensions to avoid noise, but let validator handle rejection
        return self.ingest_files(paths, embed_and_store=embed_and_store)

    def _ingest_single(self, path: Path, embedder, vector_store) -> IngestResult:
        filename = path.name
        source_str = str(path)
        # Validation
        vr: ValidationResult = validate_file(path)
        if not vr.ok:
            return IngestResult(
                source_path=source_str,
                filename=filename,
                status="rejected" if vr.error_code in ("ALLOWLIST_REJECT", "MAGIC_MISMATCH", "SIZE_EXCEEDED", "PATH_TRAVERSAL") else "failed",
                error_code=vr.error_code,
                message=vr.message,
            )

        assert vr.sha256 and vr.file_type
        sha256 = vr.sha256
        file_type = vr.file_type
        doc_id = uuid.uuid4().hex
        ingested_at = datetime.now(timezone.utc)

        # Check for duplicate by sha256 — if already exists, reuse doc_id? For now create new doc.
        # Could deduplicate: not required for Phase 2.

        parser = PARSER_MAP.get(file_type)
        if parser is None:
            return IngestResult(
                source_path=source_str,
                filename=filename,
                status="rejected",
                error_code="UNSUPPORTED_TYPE",
                message=f"No parser for .{file_type}",
                sha256=sha256,
                file_type=file_type,
            )

        try:
            doc: NormalizedDocument = parser.parse(path, doc_id, sha256, ingested_at)
        except ValueError as e:
            msg = str(e)
            code = msg.split(":")[0] if ":" in msg else "PARSE_ERROR"
            return IngestResult(source_path=source_str, filename=filename, status="failed", error_code=code, message=msg, sha256=sha256, file_type=file_type)
        except Exception as e:
            return IngestResult(source_path=source_str, filename=filename, status="failed", error_code="PARSE_ERROR", message=str(e), sha256=sha256, file_type=file_type)

        # Chunk
        try:
            chunks = chunk_document(doc)
        except Exception as e:
            return IngestResult(source_path=source_str, filename=filename, status="failed", error_code="CHUNK_ERROR", message=str(e), sha256=sha256, file_type=file_type)

        # Persist: processed JSON + chunks.jsonl + SQLite + optional vector store
        try:
            self._persist(doc, chunks)
        except Exception as e:
            return IngestResult(source_path=source_str, filename=filename, status="failed", error_code="PERSIST_ERROR", message=str(e), sha256=sha256, file_type=file_type)

        # Embed + vector store
        if embedder is not None and vector_store is not None and chunks:
            try:
                texts = [c.text for c in chunks]
                embeddings = embedder.embed(texts)
                vector_store.upsert(chunks, embeddings)
            except Exception as e:
                # Chunk persistence succeeded but embedding failed — report as ingested with warning
                # We do not fail the whole ingestion; log as warning in per-file message
                return IngestResult(
                    source_path=source_str,
                    filename=filename,
                    status="ingested",
                    document_id=doc_id,
                    file_type=file_type,
                    sha256=sha256,
                    pages=len(doc.pages),
                    chunks=len(chunks),
                    message=f"embedded_failed: {e}",
                )

        return IngestResult(
            source_path=source_str,
            filename=filename,
            status="ingested",
            document_id=doc_id,
            file_type=file_type,
            sha256=sha256,
            pages=len(doc.pages),
            chunks=len(chunks),
        )

    def _persist(self, doc: NormalizedDocument, chunks) -> None:
        # processed JSON
        doc_path = self.processed_dir / f"{doc.document_id}.json"
        chunks_path = self.processed_dir / f"{doc.document_id}.chunks.jsonl"
        # Use pydantic model_dump with json serialization for datetime
        with doc_path.open("w", encoding="utf-8") as f:
            json.dump(doc.model_dump(mode="json"), f, indent=2, ensure_ascii=False)
        with chunks_path.open("w", encoding="utf-8") as f:
            for c in chunks:
                f.write(json.dumps(c.model_dump(mode="json"), ensure_ascii=False) + "\n")

        # SQLite
        conn = get_connection(self.db_path)
        try:
            from backend.app.store.db import insert_document, insert_chunks

            insert_document(conn, doc)
            if chunks:
                insert_chunks(conn, chunks)
            conn.commit()
        finally:
            conn.close()
