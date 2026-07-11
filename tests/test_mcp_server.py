"""Contract tests for the MCP-to-REST adapter."""

import pytest

from mcp_server import server


def test_headers_read_api_key_file(monkeypatch, tmp_path):
    key_file = tmp_path / "api-key"
    key_file.write_text("mcp-local-key")
    monkeypatch.delenv("HUGINN_API_KEY", raising=False)
    monkeypatch.setenv("HUGINN_API_KEY_FILE", str(key_file))

    assert server._headers()["Authorization"] == "Bearer mcp-local-key"


@pytest.mark.asyncio
async def test_sweep_accepts_canonical_id(monkeypatch):
    calls = []

    async def fake_post(path, payload):
        calls.append((path, payload))
        return {"success": True, "id": "crawl-123"}

    async def fake_poll(path, ctx=None):
        calls.append((path, None))
        return {"success": True, "status": "completed"}

    monkeypatch.setattr(server, "_post_json", fake_post)
    monkeypatch.setattr(server, "_poll_job", fake_poll)

    result = await server.sweep("https://example.com")

    assert result["status"] == "completed"
    assert calls[1][0] == "/v1/sweep/crawl-123"


@pytest.mark.asyncio
async def test_distill_accepts_canonical_id(monkeypatch):
    async def fake_post(path, payload):
        return {"success": True, "id": "extract-123"}

    async def fake_poll(path, ctx=None):
        assert path == "/v1/distill/extract-123"
        return {"success": True, "status": "completed"}

    monkeypatch.setattr(server, "_post_json", fake_post)
    monkeypatch.setattr(server, "_poll_job", fake_poll)
    result = await server.distill(["https://example.com"], "Extract title")
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_seek_always_sends_requested_limit(monkeypatch):
    seen = {}

    async def fake_post(path, payload):
        seen.update(payload)
        return {"success": True, "data": []}

    monkeypatch.setattr(server, "_post_json", fake_post)
    await server.seek("browser automation", limit=10)
    assert seen["search_options"] == {"limit": 10}


@pytest.mark.asyncio
async def test_flock_returns_synchronous_response_without_polling(monkeypatch):
    async def fake_post(path, payload):
        assert path == "/v1/flock"
        return {"success": True, "data": [{"url": payload["urls"][0]}]}

    async def should_not_poll(*args, **kwargs):
        raise AssertionError("synchronous /v1/flock must not be polled")

    monkeypatch.setattr(server, "_post_json", fake_post)
    monkeypatch.setattr(server, "_poll_job", should_not_poll)
    result = await server.flock(["https://example.com"])
    assert result["success"] is True
