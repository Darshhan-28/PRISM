"""VectorStore protocol + ChromaStore implementation."""

from pathlib import Path
from typing import Protocol

from backend.app.config import get_config
from backend.app.ingestion.models import Chunk

try:
    import chromadb

    _HAS_CHROMA = True
except ImportError:
    _HAS_CHROMA = False


class VectorStore(Protocol):
    def upsert(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None: ...
    def search(self, query_embedding: list[float], top_k: int = 5, filters: dict | None = None) -> list[dict]: ...
    def delete(self, document_id: str) -> int: ...
    def count(self) -> int: ...


class ChromaStore:
    def __init__(self, persist_dir: Path | None = None, collection: str | None = None):
        if not _HAS_CHROMA:
            raise ImportError("chromadb not installed — pip install chromadb")
        cfg = get_config()
        self.persist_dir = Path(persist_dir or cfg.vector_store_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection or cfg.vector_store_collection
        self.client = chromadb.PersistentClient(path=str(self.persist_dir))
        self.collection = self.client.get_or_create_collection(name=self.collection_name)

    def upsert(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        if not chunks:
            return
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings length mismatch")
        ids = [c.chunk_id for c in chunks]
        docs = [c.text for c in chunks]
        metas = []
        for c in chunks:
            m = c.metadata
            meta = {
                "document_id": m.document_id,
                "filename": m.filename,
                "file_type": m.file_type,
                "source_path": m.source_path,
                "sha256": m.sha256,
                "chunk_id": m.chunk_id,
                "page_number": m.page_number if m.page_number is not None else -1,
                "section": m.section or "",
                "title": m.title or "",
                "chunk_index": m.chunk_index,
            }
            metas.append(meta)
        self.collection.upsert(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)

    def search(self, query_embedding: list[float], top_k: int = 5, filters: dict | None = None) -> list[dict]:
        where = None
        if filters:
            # chroma where expects exact matches
            where = filters
        res = self.collection.query(query_embeddings=[query_embedding], n_results=top_k, where=where)
        out: list[dict] = []
        if not res or not res.get("ids") or not res["ids"][0]:
            return out
        ids = res["ids"][0]
        docs = res["documents"][0] if res.get("documents") else [None] * len(ids)
        metas = res["metadatas"][0] if res.get("metadatas") else [None] * len(ids)
        dists = res["distances"][0] if res.get("distances") else [None] * len(ids)
        for i, cid in enumerate(ids):
            out.append(
                {
                    "chunk_id": cid,
                    "text": docs[i] if i < len(docs) else None,
                    "metadata": metas[i] if i < len(metas) else None,
                    "distance": dists[i] if i < len(dists) else None,
                }
            )
        return out

    def delete(self, document_id: str) -> int:
        # chroma delete by where
        existing = self.collection.get(where={"document_id": document_id})
        ids = existing.get("ids", []) if existing else []
        if ids:
            self.collection.delete(ids=ids)
        return len(ids)

    def count(self) -> int:
        return self.collection.count()

    def reset(self) -> None:
        # for tests — delete collection
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass
        self.collection = self.client.get_or_create_collection(name=self.collection_name)


def get_vector_store() -> VectorStore:
    cfg = get_config()
    provider = cfg.vector_store_provider.lower()
    if provider == "chroma":
        return ChromaStore()
    else:
        raise ValueError(f"Unknown vector_store_provider: {provider}")
