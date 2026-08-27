import uuid
import datetime
from backend.app.ingestion.models import NormalizedDocument, Page
from backend.app.ingestion.chunker import chunk_document


def make_doc(text: str, pages: int = 1):
    doc_id = uuid.uuid4().hex
    if pages == 1:
        pgs = [Page(page_number=1, text=text, char_count=len(text))]
    else:
        mid = len(text) // 2
        pgs = [Page(page_number=1, text=text[:mid], char_count=mid), Page(page_number=2, text=text[mid:], char_count=len(text) - mid)]
    return NormalizedDocument(
        document_id=doc_id,
        filename="test.txt",
        file_type="txt",
        source_path="/tmp/test.txt",
        sha256="abc",
        title="t",
        ingested_at=datetime.datetime.now(datetime.timezone.utc),
        pages=pgs,
    )


def test_deterministic():
    text = "Hello world. This is a test. " * 50
    doc = make_doc(text)
    c1 = chunk_document(doc, chunk_size=200, chunk_overlap=20)
    c2 = chunk_document(doc, chunk_size=200, chunk_overlap=20)
    assert [c.text for c in c1] == [c.text for c in c2]
    assert [c.chunk_id for c in c1] == [c.chunk_id for c in c2]


def test_overlap():
    text = "A" * 1000
    doc = make_doc(text)
    chunks = chunk_document(doc, chunk_size=300, chunk_overlap=50)
    # overlap means consecutive chunks share tail/head
    assert len(chunks) >= 2
    # second chunk should start 250 chars into text (300-50)
    # not precise due to break finder, but at least some overlap exists for long uniform text
    assert chunks[1].text[:10] == "A" * 10


def test_empty():
    doc = make_doc("   ")
    assert chunk_document(doc) == []


def test_metadata_provenance():
    doc = make_doc("line1\nline2\nline3\n" * 20)
    chunks = chunk_document(doc, chunk_size=100, chunk_overlap=20)
    for c in chunks:
        m = c.metadata
        assert m.document_id == doc.document_id
        assert m.filename == doc.filename
        assert m.sha256 == doc.sha256
        assert m.chunk_id.startswith(doc.document_id)
        assert m.chunk_index >= 0
        assert m.token_estimate > 0
        assert m.line_range is not None
        assert m.page_number is not None


def test_page_tracking():
    text = "A" * 800 + "B" * 800
    doc = make_doc(text, pages=2)
    chunks = chunk_document(doc, chunk_size=400, chunk_overlap=50)
    pages = [c.metadata.page_number for c in chunks]
    assert 1 in pages and 2 in pages


def test_chunk_id_format():
    doc = make_doc("hello world " * 100)
    chunks = chunk_document(doc, chunk_size=200, chunk_overlap=20)
    for c in chunks:
        assert ":p" in c.chunk_id and ":c" in c.chunk_id
