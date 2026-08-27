"""Parser protocol and registry."""

from typing import Protocol
from pathlib import Path

from backend.app.ingestion.models import NormalizedDocument


class Parser(Protocol):
    def parse(self, path: Path, doc_id: str, sha256: str, ingested_at) -> NormalizedDocument:
        ...
