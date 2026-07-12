"""Authenticated StarSearch browser-session gateway tests."""

from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from huginn.api import create_app
from huginn.config import HuginnConfig
from huginn.proxy import ProxyEndpoint, StaticProxyProvider
from huginn.state import get_state, reset_state


def _egress(proxy: dict | None = None) -> dict:
    proxied = bool(proxy)
    return {
        "gateway_enforced": True,
        "mode": "upstream" if proxied else "direct",
        "upstream_scheme": "http" if proxied else None,
        "upstream_identity": (
            hashlib.sha256(
                f"{proxy['server']}\0{proxy.get('username', '')}".encode()
            ).hexdigest()
            if proxy
            else None
        ),
        "resolution": "local_frozen",
    }


@pytest.fixture(autouse=True)
def clean_state():
    reset_state()
    yield
    reset_state()


@pytest.fixture
def daemon(monkeypatch):
    command = MagicMock()
    closed_sids: set[str] = set()

    async def respond(payload, timeout=30.0):
        command(payload, timeout=timeout)
        if payload["cmd"] == "new_session":
            result = {
                "sid": "sid-123",
                "daemon_instance_id": "a" * 32,
                "egress": _egress(payload.get("opts", {}).get("proxy")),
            }
            context = payload.get("opts", {}).get("context")
            if context:
                result["context"] = {
                    "name": context["name"],
                    "persistent": True,
                    "created": True,
                    "scope": "host",
                    "profile_persistent": True,
                    "runtime_restart_survival": False,
                }
            return {"ok": True, "sid": "sid-123", "result": result}
        if payload["cmd"] == "list_contexts":
            return {
                "ok": True,
                "result": {
                    "contexts": [
                        {
                            "name": "hermes-research",
                            "created_at": 1_700_000_000,
                            "last_opened_at": 1_700_000_100,
                            "locale": "en-US",
                            "profile_persistent": True,
                            "runtime_restart_survival": False,
                            "scope": "host",
                            "active": False,
                            "quarantined": False,
                            "quarantine": None,
                            "idle_seconds": 100,
                            "retention_expires_at": 1_707_776_100,
                            "retention_expired": False,
                            "prune_eligible": False,
                        }
                    ]
                },
            }
        if payload["cmd"] == "prune_contexts":
            return {
                "ok": True,
                "result": {
                    "dry_run": payload["dry_run"],
                    "total_before": 2,
                    "total_after": 2 if payload["dry_run"] else 1,
                    "max_contexts": 100,
                    "retention_seconds": 7_776_000,
                    "candidates": [
                        {
                            "name": "expired-context",
                            "last_opened_at": 1_600_000_000,
                            "reasons": ["retention_expired"],
                        }
                    ],
                    "deleted": [] if payload["dry_run"] else ["expired-context"],
                    "protected_active": [],
                    "protected_quarantined": ["quarantined-context"],
                    "remaining_over_limit": 0,
                },
            }
        if payload["cmd"] == "recover_context":
            return {
                "ok": True,
                "result": {
                    "name": payload["name"],
                    "recovered": True,
                    "stale_profile_locks_removed": ["SingletonLock"],
                },
            }
        if payload["cmd"] == "delete_context":
            return {
                "ok": True,
                "result": {"name": payload["name"], "deleted": True},
            }
        if payload["cmd"] == "close_session":
            sid = payload.get("sid", "")
            already_closed = sid in closed_sids
            closed_sids.add(sid)
            return {
                "ok": True,
                "sid": sid,
                "result": {
                    "closed": True,
                    "already_closed": already_closed,
                    "warnings": [],
                },
            }
        return {
            "ok": True,
            "sid": payload.get("sid", ""),
            "result": {"url": payload.get("url", "https://example.com")},
            "security": {},
        }

    monkeypatch.setattr("huginn.routers.browser_sessions.daemon_command", respond)
    monkeypatch.setattr(
        "huginn.routers.browser_sessions.daemon_status",
        AsyncMock(
            return_value={
                "configured": True,
                "reachable": True,
                "active_sessions": 1,
                "instance_id": "a" * 32,
            }
        ),
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
    assert created.json()["egress"] == _egress()
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
        "egress": _egress(),
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
        response = await client.post("/v1/browser/sessions", json={"allow_internal_network": True})

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


@pytest.mark.asyncio
async def test_persistent_context_requires_configured_auth(daemon):
    app = create_app(HuginnConfig())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create = await client.post("/v1/browser/sessions", json={"context_id": "hermes-research"})
        listed = await client.get("/v1/browser/contexts")
        deleted = await client.delete("/v1/browser/contexts/hermes-research")

    for response in (create, listed, deleted):
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "persistent_context_auth_required"
    assert daemon.call_count == 0


@pytest.mark.asyncio
async def test_named_context_uses_stable_provider_lease_and_typed_routes(daemon, monkeypatch):
    config = HuginnConfig()
    config.server.api_key = "test-secret"
    lease = MagicMock()
    lease.as_browser_proxy.return_value = None
    provider = MagicMock()
    provider.acquire.return_value = lease
    monkeypatch.setattr(
        "huginn.routers.browser_sessions.get_proxy_provider", lambda _config: provider
    )
    app = create_app(config)
    headers = {"Authorization": "Bearer test-secret"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/v1/browser/sessions",
            json={
                "context_id": "hermes-research",
                "context_mode": "create_new",
                "max_idle_s": 120,
            },
            headers=headers,
        )
        listed = await client.get("/v1/browser/contexts", headers=headers)
        deleted = await client.delete("/v1/browser/contexts/hermes-research", headers=headers)

    assert created.status_code == 200
    assert created.json()["context"] == {
        "context_id": "hermes-research",
        "id": "hermes-research",
        "persistent": True,
        "created": True,
        "scope": "host",
        "profile_persistent": True,
        "runtime_restart_survival": False,
    }
    provider.acquire.assert_called_once_with(
        session_key="browser-context:hermes-research",
        strict_sticky=True,
    )
    calls = [call.args[0] for call in daemon.call_args_list]
    assert calls[0]["opts"]["context"] == {
        "name": "hermes-research",
        "mode": "create_new",
    }
    assert listed.status_code == 200
    assert listed.json()["contexts"][0]["context_id"] == "hermes-research"
    # Deprecated compatibility alias remains during the schema transition.
    assert listed.json()["contexts"][0]["name"] == "hermes-research"
    assert listed.json()["contexts"][0]["quarantined"] is False
    assert listed.json()["contexts"][0]["created_at"].startswith("2023-11-14T")
    assert deleted.json() == {
        "success": True,
        "context_id": "hermes-research",
        "name": "hermes-research",
        "status": "deleted",
    }


@pytest.mark.asyncio
async def test_context_prune_and_confirmed_recovery_are_typed_and_fail_closed(daemon):
    config = HuginnConfig()
    config.server.api_key = "test-secret"
    app = create_app(config)
    headers = {"Authorization": "Bearer test-secret"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        plan = await client.post("/v1/browser/contexts/prune", json={}, headers=headers)
        refused = await client.post(
            "/v1/browser/contexts/quarantined-context/recover",
            json={"confirm": False},
            headers=headers,
        )
        recovered = await client.post(
            "/v1/browser/contexts/quarantined-context/recover",
            json={"confirm": True},
            headers=headers,
        )
        applied = await client.post(
            "/v1/browser/contexts/prune",
            json={"dry_run": False},
            headers=headers,
        )

    assert plan.status_code == 200
    assert plan.json()["dry_run"] is True
    assert plan.json()["deleted"] == []
    assert plan.json()["candidates"][0]["name"] == "expired-context"
    assert plan.json()["protected_quarantined"] == ["quarantined-context"]
    assert refused.status_code == 400
    assert refused.json()["detail"]["code"] == "recovery_confirmation_required"
    assert recovered.json() == {
        "success": True,
        "context_id": "quarantined-context",
        "name": "quarantined-context",
        "recovered": True,
        "stale_profile_locks_removed": ["SingletonLock"],
    }
    assert applied.json()["dry_run"] is False
    assert applied.json()["deleted"] == ["expired-context"]
    calls = [call.args[0] for call in daemon.call_args_list]
    assert sum(call["cmd"] == "recover_context" for call in calls) == 1
    assert calls[-1] == {"v": 1, "cmd": "prune_contexts", "dry_run": False}


@pytest.mark.asyncio
async def test_proxy_health_only_recovers_after_successful_network_command(daemon, monkeypatch):
    endpoint = ProxyEndpoint.parse("http://account:secret@proxy.example:8080")
    provider = StaticProxyProvider([endpoint], failure_threshold=3, cooldown_seconds=60)
    monkeypatch.setattr(
        "huginn.routers.browser_sessions.get_proxy_provider", lambda _config: provider
    )

    navigate_attempts = 0

    async def respond(payload, timeout=30.0):
        nonlocal navigate_attempts
        if payload["cmd"] == "new_session":
            return {
                "ok": True,
                "sid": "sid-proxy",
                "result": {
                    "sid": "sid-proxy",
                    "daemon_instance_id": "a" * 32,
                    "egress": _egress(
                        {
                            "server": "http://proxy.example:8080",
                            "username": "account",
                        }
                    ),
                },
            }
        if payload["cmd"] == "navigate":
            navigate_attempts += 1
            if navigate_attempts == 1:
                raise RuntimeError("ERR_PROXY_CONNECTION_FAILED")
        if payload["cmd"] == "close_session":
            return {"ok": True, "result": {"closed": True, "warnings": []}}
        return {"ok": True, "result": {}, "security": {}}

    monkeypatch.setattr("huginn.routers.browser_sessions.daemon_command", respond)
    app = create_app(HuginnConfig())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/v1/browser/sessions", json={})
        after_create = provider.status()["endpoints"][0].copy()

        failed = await client.post(
            "/v1/browser/sessions/sid-proxy/commands",
            json={"command": "navigate", "url": "https://example.com"},
        )
        after_failure = provider.status()["endpoints"][0].copy()

        local_navigation = await client.post(
            "/v1/browser/sessions/sid-proxy/commands",
            json={"command": "navigate", "url": "data:text/html,local"},
        )
        after_local_navigation = provider.status()["endpoints"][0].copy()

        content = await client.post(
            "/v1/browser/sessions/sid-proxy/commands",
            json={"command": "get_content"},
        )
        after_dom_command = provider.status()["endpoints"][0].copy()

        navigated = await client.post(
            "/v1/browser/sessions/sid-proxy/commands",
            json={"command": "navigate", "url": "https://example.com"},
        )
        after_navigation = provider.status()["endpoints"][0].copy()

        closed = await client.delete("/v1/browser/sessions/sid-proxy")
        after_close = provider.status()["endpoints"][0].copy()

    assert created.status_code == 200
    assert after_create["successes"] == 0
    assert after_create["failures"] == 0

    assert failed.status_code == 502
    assert after_failure["successes"] == 0
    assert after_failure["failures"] == 1
    assert after_failure["consecutive_failures"] == 1

    assert local_navigation.status_code == 200
    assert after_local_navigation["successes"] == 0
    assert after_local_navigation["failures"] == 1
    assert after_local_navigation["consecutive_failures"] == 1

    assert content.status_code == 200
    assert after_dom_command["successes"] == 0
    assert after_dom_command["failures"] == 1
    assert after_dom_command["consecutive_failures"] == 1

    assert navigated.status_code == 200
    assert after_navigation["successes"] == 1
    assert after_navigation["failures"] == 1
    assert after_navigation["consecutive_failures"] == 0

    assert closed.status_code == 200
    assert after_close == after_navigation


@pytest.mark.asyncio
async def test_named_context_rejects_raw_proxy_and_invalid_names(daemon):
    config = HuginnConfig()
    config.server.api_key = "test-secret"
    app = create_app(config)
    headers = {"Authorization": "Bearer test-secret"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        raw_proxy = await client.post(
            "/v1/browser/sessions",
            json={
                "context_id": "hermes-research",
                "proxy": "http://proxy.example:8080",
            },
            headers=headers,
        )
        invalid_create = await client.post(
            "/v1/browser/sessions",
            json={"context_id": "../escape"},
            headers=headers,
        )
        mode_without_context = await client.post(
            "/v1/browser/sessions",
            json={"context_mode": "open_existing"},
            headers=headers,
        )
        invalid_delete = await client.delete("/v1/browser/contexts/UPPER", headers=headers)

    assert raw_proxy.status_code == 422
    assert raw_proxy.json()["detail"]["code"] == "context_proxy_override_not_allowed"
    assert invalid_create.status_code == 422
    assert mode_without_context.status_code == 422
    assert mode_without_context.json()["detail"]["code"] == "context_id_required"
    assert invalid_delete.status_code == 422
    assert invalid_delete.json()["detail"]["code"] == "invalid_context_name"
    assert daemon.call_count == 0


@pytest.mark.asyncio
async def test_close_failure_retains_retry_handle(monkeypatch):
    attempts = 0

    async def close(payload, timeout=30.0):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary daemon failure")
        return {"ok": True, "result": {"closed": True}}

    monkeypatch.setattr("huginn.routers.browser_sessions.daemon_command", close)
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
        "egress": _egress(),
    }
    app = create_app(HuginnConfig())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        failed = await client.delete("/v1/browser/sessions/sid-123")
        retained = await client.get("/v1/browser/sessions/sid-123")
        retried = await client.delete("/v1/browser/sessions/sid-123")

    assert failed.status_code == 502
    assert failed.json()["detail"]["code"] == "browser_session_close_failed"
    assert retained.status_code == 200
    assert retained.json()["status"] == "close_failed"
    assert retried.json()["status"] == "closed"
    assert "sid-123" not in get_state().browser_sessions


@pytest.mark.asyncio
async def test_commands_are_serialized_per_session(monkeypatch):
    active = 0
    maximum_active = 0

    async def command(payload, timeout=30.0):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {"ok": True, "result": {"url": "https://example.com"}, "security": {}}

    monkeypatch.setattr("huginn.routers.browser_sessions.daemon_command", command)
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
        "egress": _egress(),
    }
    app = create_app(HuginnConfig())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first, second = await asyncio.gather(
            client.post(
                "/v1/browser/sessions/sid-123/commands",
                json={"command": "get_content"},
            ),
            client.post(
                "/v1/browser/sessions/sid-123/commands",
                json={"command": "screenshot"},
            ),
        )

    assert first.status_code == second.status_code == 200
    assert maximum_active == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("daemon_error", "expected_code"),
    [
        ("ContextInUse", "context_in_use"),
        ("ContextQuarantined", "context_quarantined"),
    ],
)
async def test_context_daemon_errors_map_to_stable_codes(
    monkeypatch, daemon_error, expected_code
):
    async def fail(payload, timeout=30.0):
        raise RuntimeError(
            f"StarSearch delete_context failed: {daemon_error}: hermes-research"
        )

    monkeypatch.setattr("huginn.routers.browser_sessions.daemon_command", fail)
    config = HuginnConfig()
    config.server.api_key = "test-secret"
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete(
            "/v1/browser/contexts/hermes-research",
            headers={"Authorization": "Bearer test-secret"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == expected_code


@pytest.mark.asyncio
async def test_named_context_does_not_invent_missing_daemon_capabilities(monkeypatch):
    calls = []

    async def old_daemon(payload, timeout=30.0):
        calls.append(payload)
        if payload["cmd"] == "new_session":
            return {
                "ok": True,
                "sid": "sid-old",
                "result": {
                    "sid": "sid-old",
                    "context": {"name": "hermes-research"},
                },
            }
        return {"ok": True, "result": {"closed": True}}

    monkeypatch.setattr("huginn.routers.browser_sessions.daemon_command", old_daemon)
    config = HuginnConfig()
    config.server.api_key = "test-secret"
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/browser/sessions",
            json={"context_id": "hermes-research"},
            headers={"Authorization": "Bearer test-secret"},
        )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "invalid_daemon_response"
    assert calls[-1] == {"v": 1, "cmd": "close_session", "sid": "sid-old"}
    assert "sid-old" not in get_state().browser_sessions


@pytest.mark.asyncio
async def test_starsearch_restart_marks_session_interrupted(daemon, monkeypatch):
    config = HuginnConfig()
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/v1/browser/sessions", json={})
        assert created.status_code == 200
        monkeypatch.setattr(
            "huginn.routers.browser_sessions.daemon_status",
            AsyncMock(
                return_value={
                    "configured": True,
                    "reachable": True,
                    "instance_id": "b" * 32,
                }
            ),
        )
        fetched = await client.get("/v1/browser/sessions/sid-123")
        command = await client.post(
            "/v1/browser/sessions/sid-123/commands",
            json={"command": "screenshot"},
        )

    assert fetched.status_code == 200
    assert fetched.json()["status"] == "interrupted"
    assert command.status_code == 410
    assert command.json()["detail"]["code"] == "browser_session_interrupted"


@pytest.mark.asyncio
async def test_untracked_session_close_still_contacts_daemon(daemon):
    app = create_app(HuginnConfig())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/v1/browser/sessions/orphan-sid")

    assert response.status_code == 200
    assert response.json()["status"] == "closed"
    assert daemon.call_args.args[0] == {
        "v": 1,
        "cmd": "close_session",
        "sid": "orphan-sid",
    }
