from backend.app.llm.mock_adapter import MockAdapter


def test_mock_basic():
    m = MockAdapter()
    assert m.provider == "mock"
    assert m.health_check()["ok"] is True
    out = m.generate("hello")
    assert "MOCK" in out


def test_mock_canned():
    m = MockAdapter(canned={"pressure": "Supported: 2.1 bar"})
    assert m.generate("what is pressure?") == "Supported: 2.1 bar"
    assert "MOCK" in m.generate("other query")


def test_mock_sop_heuristic():
    m = MockAdapter()
    out = m.generate("Explain SOP pressure")
    assert "SOP" in out and "2.1" in out


def test_prompt_validation_empty():
    m = MockAdapter()
    try:
        m.generate("")
        assert False
    except ValueError:
        pass
    try:
        m.generate("   ")
        assert False
    except ValueError:
        pass


def test_prompt_too_long():
    m = MockAdapter()
    try:
        m.generate("a" * 8001)
        assert False
    except ValueError as e:
        assert "too long" in str(e)


def test_system_validation():
    m = MockAdapter()
    try:
        m.generate("hi", system="x" * 2001)
        assert False
    except ValueError:
        pass


def test_mock_stream():
    import asyncio

    async def run():
        m = MockAdapter()
        chunks = []
        async for ch in m.generate_stream("hello world"):
            chunks.append(ch)
        assert "".join(chunks) == m.generate("hello world")
        assert len(chunks) == 2

    asyncio.run(run())
