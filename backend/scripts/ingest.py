"""CLI — python -m backend.scripts.ingest data/raw/samples --recreate"""

import argparse
import shutil
import sys
from pathlib import Path

# Ensure project root on path when run as `python -m backend.scripts.ingest`
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.config import get_config
from backend.app.ingestion.pipeline import IngestionPipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="SIH26117 local ingestion CLI")
    parser.add_argument("input", nargs="?", default=None, help="File or directory to ingest (default: raw_dir)")
    parser.add_argument("--recreate", action="store_true", help="Delete processed, vector_store, and DB before ingest")
    parser.add_argument("--no-embed", action="store_true", help="Skip embedding/vector store (pipeline only)")
    parser.add_argument("--recursive", action="store_true", default=True, help="Recursive directory ingest")
    args = parser.parse_args()

    cfg = get_config()
    if args.recreate:
        if cfg.processed_dir.exists():
            shutil.rmtree(cfg.processed_dir)
        cfg.processed_dir.mkdir(parents=True, exist_ok=True)
        (cfg.processed_dir / ".gitkeep").write_text("")
        if cfg.vector_store_dir.exists():
            shutil.rmtree(cfg.vector_store_dir)
        cfg.vector_store_dir.mkdir(parents=True, exist_ok=True)
        (cfg.vector_store_dir / ".gitkeep").write_text("")
        if cfg.db_path.exists():
            cfg.db_path.unlink()
        print(f"[recreate] cleared {cfg.processed_dir}, {cfg.vector_store_dir}, {cfg.db_path}")

    target = Path(args.input) if args.input else cfg.raw_dir
    if not target.exists():
        print(f"Input not found: {target}", file=sys.stderr)
        sys.exit(1)

    pipeline = IngestionPipeline()
    embed = not args.no_embed
    if target.is_file():
        report = pipeline.ingest_files([target], embed_and_store=embed)
    else:
        report = pipeline.ingest_directory(target, recursive=args.recursive, embed_and_store=embed)

    print(f"Ingested {report.ingested} documents, {report.total_chunks} chunks, {report.rejected} rejected, {report.failed} failed in {report.duration_ms}ms")
    for r in report.per_file:
        status = r.status
        extra = f" pages={r.pages} chunks={r.chunks}" if r.status == "ingested" else f" error={r.error_code}: {r.message}"
        print(f"  {r.filename:30s} -> {status:10s}{extra}  sha256={r.sha256[:8] if r.sha256 else '-'}")

    if embed:
        from backend.app.retrieval.vector_store import get_vector_store

        try:
            vs = get_vector_store()
            print(f"Vector store: {vs.count()} embeddings at {cfg.vector_store_dir}")
        except Exception as e:
            print(f"Vector store check failed: {e}")


if __name__ == "__main__":
    main()
