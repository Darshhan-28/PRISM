import math
from backend.app.retrieval.embedder import MockEmbedder


def test_dim():
    m = MockEmbedder(dim=384)
    assert m.dim == 384


def test_deterministic():
    m = MockEmbedder(dim=32)
    v1 = m.embed(["hello world"])[0]
    v2 = m.embed(["hello world"])[0]
    assert v1 == v2


def test_l2_normalized():
    m = MockEmbedder(dim=64)
    vecs = m.embed(["test one", "test two"])
    for v in vecs:
        norm = math.sqrt(sum(x * x for x in v))
        assert abs(norm - 1.0) < 1e-6


def test_self_cosine_near_one():
    m = MockEmbedder(dim=128)
    v = m.embed(["same text", "same text"])
    cos = sum(a * b for a, b in zip(v[0], v[1]))
    assert abs(cos - 1.0) < 1e-6


def test_different_texts_not_identical():
    m = MockEmbedder(dim=64)
    v0, v1 = m.embed(["hello", "world"])
    assert v0 != v1


def test_batch_length():
    m = MockEmbedder(dim=16)
    texts = ["a", "b", "c"]
    vecs = m.embed(texts)
    assert len(vecs) == 3
    assert all(len(v) == 16 for v in vecs)
