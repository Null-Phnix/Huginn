"""Authenticated StarSearch browser-session gateway tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from huginn.api import create_app
from huginn.config import HuginnConfig
from huginn.state import get_state, reset_state


@pytest.fixture(autouse=True)
def clean_state():
    reset_state()
    yield
    reset_state()


@pytest.fixture
def daemon(monkeypatch):
    command = MagicMock()

    async def respond(payload, timeout=30.0):
        command(payload, timeout=timeout)
        if payload["cmd"] == "new_session":
            return {"ok": True, "sid": "sid-123", "result": {"sid": "sid-123"}}
        return {
            "ok": True,
            "sid": payload.get("sid", ""),
            "result": {"url": payload.get("url", "https://example.com")},
            "security": {},
        }

    monkeypatch.setattr("huginn.routers.browser_sessions.daemon_command", respond)
    monkeypatch.setattr(
        "huginn.routers.browser_sessions.daemon_status",
        AsyncMock(return_value={"configured": True, "reachable": True, "active_sessions": 1}),
    )
    return command


@pytest.mark.asyncio
async def test_session_lifecycle_and_command_mapping(daemon):
    app = create_app(HuginnConfig())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/v1/browser/sessions",
            json={"allowed_domains": ["example.com"], "max_idle_s": 120},
        )
        navigated = await client.post(
            "/v1/browser/sessions/sid-123/commands",
            json={"command": "navigate", "url": "https://example.com/path", "timeout_s": 12},
        )
        fetched = await client.get("/v1/browser/sessions/sid-123")
        listed = await client.get("/v1/browser/sessions")
        closed = await client.delete("/v1/browser/sessions/sid-123")
        closed_again = await client.delete("/v1/browser/sessions/sid-123")

    assert created.status_code == 200
    assert created.json()["id"] == "sid-123"
    assert created.json()["proxy_configured"] is False
    assert navigated.status_code == 200
    assert navigated.json()["result"]["url"] == "https://example.com/path"
    assert fetched.status_code == 200
    assert len(listed.json()["sessions"]) == 1
    assert closed.json()["status"] == "closed"
    assert closed_again.json()["status"] == "already_closed"

    calls = [call.args[0] for call in daemon.call_args_list]
    assert calls[0]["cmd"] == "new_session"
    assert calls[0]["opts"]["capabilities"]["allowed_domains"] == ["example.com"]
    assert calls[1] == {
        "v": 1,
        "cmd": "navigate",
        "sid": "sid-123",
        "url": "https://example.com/path",
        "timeout_s": 12,
    }
    assert calls[-1]["cmd"] == "close_session"


@pytest.mark.asyncio
async def test_command_requires_command_specific_fields(daemon):
    get_state().browser_sessions["sid-123"] = {
        "created_at": 1_700_000_000.0,
        "last_active_at": 1_700_000_000.0,
        "expires_at": 4_700_000_000.0,
        "max_idle_s": 120,
        "locale": "en-US",
        "human_level": 1,
        "allowed_domains": [],
        "allow_internal_network": False,
        "proxy_configured": False,
    }
    app = create_app(HuginnConfig())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/browser/sessions/sid-123/commands", json={"command": "click"}
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "missing_command_field"


@pytest.mark.asyncio
async def test_internal_network_requires_server_policy(daemon):
    app = create_app(HuginnConfig())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/browser/sessions", json={"allow_internal_network": True}
        )

    assert response.status_code == 403
    assert daemon.call_count == 0


@pytest.mark.asyncio
async def test_api_auth_applies_to_browser_sessions(daemon):
    config = HuginnConfig()
    config.server.api_key = "test-secret"
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.post("/v1/browser/sessions", json={})
        accepted = await client.post(
            "/v1/browser/sessions",
            json={},
            headers={"Authorization": "Bearer test-secret"},
        )

    assert missing.status_code == 401
    assert accepted.status_code == 200


@pytest.mark.asyncio
async def test_create_failure_is_structured_and_redacts_proxy(monkeypatch):
    proxy = "http://user:password@proxy.invalid:8080"

    async def fail(payload, timeout=30.0):
        raise RuntimeError(f"BrowserLaunchFailed using {proxy}")

    monkeypatch.setattr("huginn.routers.browser_sessions.daemon_command", fail)
    app = create_app(HuginnConfig())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/browser/sessions", json={"proxy": proxy})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["layer"] == "starsearch"
    assert proxy not in detail["message"]
    assert "[redacted]" in detail["message"]
