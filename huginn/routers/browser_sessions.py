"""Authenticated, local Browserbase-style session lifecycle over StarSearch."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..config import HuginnConfig
from ..models import (
    BrowserCommand,
    BrowserSessionCommandRequest,
    BrowserSessionCreateRequest,
)
from ..starsearch_scrape import daemon_command, daemon_status
from ..state import get_state
from ..proxy import ProxyConfigurationError, ProxyEndpoint, ProxyUnavailable
from ..utils import get_proxy_provider, proxy_failure_likely


def _timestamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _public_session(session_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": session_id,
        "status": "active",
        "created_at": _timestamp(metadata["created_at"]),
        "last_active_at": _timestamp(metadata["last_active_at"]),
        "expires_at": _timestamp(metadata["expires_at"]),
        "locale": metadata["locale"],
        "human_level": metadata["human_level"],
        "allowed_domains": metadata["allowed_domains"],
        "allow_internal_network": metadata["allow_internal_network"],
        "proxy_configured": metadata["proxy_configured"],
    }


def _require(value: Any, field: str, command: BrowserCommand) -> Any:
    if value is None or value == "":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "missing_command_field",
                "message": f"{field} is required for {command.value}",
            },
        )
    return value


def _safe_daemon_error(exc: Exception, secret: str | None = None) -> str:
    """Keep useful daemon categories while never reflecting proxy credentials."""
    message = str(exc)
    if secret:
        message = message.replace(secret, "[redacted]")
    return message[:1000]


def _command_payload(session_id: str, req: BrowserSessionCommandRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {"v": 1, "cmd": req.command.value, "sid": session_id}
    if req.command == BrowserCommand.NAVIGATE:
        payload.update(url=_require(req.url, "url", req.command), timeout_s=req.timeout_s)
    elif req.command in {BrowserCommand.CLICK, BrowserCommand.HOVER}:
        payload["selector"] = _require(req.selector, "selector", req.command)
        if req.command == BrowserCommand.CLICK:
            payload["human"] = req.human
    elif req.command == BrowserCommand.TYPE:
        payload.update(
            selector=_require(req.selector, "selector", req.command),
            text=_require(req.text, "text", req.command),
            human=req.human,
        )
    elif req.command == BrowserCommand.SCROLL:
        payload.update(direction=req.direction, amount=req.amount)
    elif req.command == BrowserCommand.WAIT_FOR:
        payload.update(
            selector=_require(req.selector, "selector", req.command),
            timeout_s=req.timeout_s,
        )
    elif req.command == BrowserCommand.EVALUATE:
        payload["script"] = _require(req.script, "script", req.command)
    elif req.command == BrowserCommand.SET_COOKIES:
        payload["cookies"] = _require(req.cookies, "cookies", req.command)
    return payload


def create_browser_sessions_router(config: HuginnConfig, verify_api_key) -> APIRouter:
    router = APIRouter(prefix="/v1/browser", tags=["Browser Sessions"])

    @router.post("/sessions")
    async def create_session(
        req: BrowserSessionCreateRequest,
        auth=Depends(verify_api_key),
    ):
        if req.allow_internal_network and not config.browser.allow_private_network:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "internal_network_not_enabled",
                    "message": "Server policy does not allow internal-network browser sessions",
                },
            )
        proxy_lease = None
        try:
            if req.proxy:
                proxy_payload = ProxyEndpoint.parse(req.proxy).as_browser_proxy()
            else:
                proxy_lease = get_proxy_provider(config).acquire(
                    session_key=f"browser:{uuid.uuid4()}"
                )
                proxy_payload = proxy_lease.as_browser_proxy()
            response = await daemon_command(
                {
                    "v": 1,
                    "cmd": "new_session",
                    "sid": None,
                    "opts": {
                        "proxy": proxy_payload,
                        "locale": req.locale,
                        "human_level": req.human_level,
                        "injection_mode": "flag",
                        "capabilities": {
                            "navigate": True,
                            "download": False,
                            "form_submit": True,
                            "allowed_domains": req.allowed_domains,
                            "block_internal_network": not req.allow_internal_network,
                            "allow_internal_network": req.allow_internal_network,
                            "max_idle_s": req.max_idle_s,
                        },
                    },
                }
            )
        except (ProxyConfigurationError, ProxyUnavailable) as exc:
            raise HTTPException(
                status_code=422 if isinstance(exc, ProxyConfigurationError) else 503,
                detail={
                    "code": "invalid_proxy" if isinstance(exc, ProxyConfigurationError) else "proxy_unavailable",
                    "message": _safe_daemon_error(exc, req.proxy),
                    "layer": "huginn-egress",
                    "retryable": isinstance(exc, ProxyUnavailable),
                },
            ) from exc
        except Exception as exc:
            if proxy_lease and proxy_failure_likely(message=str(exc)):
                proxy_lease.report_failure(str(exc))
            message = _safe_daemon_error(exc, req.proxy)
            status = 503 if "not configured" in message.lower() else 502
            raise HTTPException(
                status_code=status,
                detail={
                    "code": "browser_session_create_failed",
                    "message": message,
                    "layer": "starsearch",
                    "retryable": True,
                },
            ) from exc
        session_id = response.get("sid") or (response.get("result") or {}).get("sid")
        if not session_id:
            raise HTTPException(
                status_code=502,
                detail={"code": "invalid_daemon_response", "message": "No session id returned"},
            )
        if proxy_lease:
            proxy_lease.report_success()
        now = time.time()
        metadata = {
            "created_at": now,
            "last_active_at": now,
            "expires_at": now + req.max_idle_s,
            "max_idle_s": req.max_idle_s,
            "locale": req.locale,
            "human_level": req.human_level,
            "allowed_domains": list(req.allowed_domains),
            "allow_internal_network": req.allow_internal_network,
            "proxy_configured": bool(proxy_payload),
            "proxy_lease": proxy_lease,
        }
        get_state().browser_sessions[session_id] = metadata
        return {"success": True, **_public_session(session_id, metadata)}

    @router.get("/sessions")
    async def list_sessions(auth=Depends(verify_api_key)):
        state = get_state()
        now = time.time()
        expired = [
            sid
            for sid, metadata in state.browser_sessions.items()
            if metadata["expires_at"] <= now
        ]
        for sid in expired:
            lease = state.browser_sessions[sid].get("proxy_lease")
            try:
                await daemon_command({"v": 1, "cmd": "close_session", "sid": sid})
                if lease:
                    lease.report_success()
            except Exception:
                pass
        active = {
            sid: metadata for sid, metadata in state.browser_sessions.items() if sid not in expired
        }
        state.browser_sessions = active
        return {
            "success": True,
            "sessions": [_public_session(sid, metadata) for sid, metadata in active.items()],
            "daemon": await daemon_status(),
        }

    @router.get("/sessions/{session_id}")
    async def get_session(session_id: str, auth=Depends(verify_api_key)):
        metadata = get_state().browser_sessions.get(session_id)
        if not metadata:
            raise HTTPException(status_code=404, detail={"code": "session_not_found"})
        if metadata["expires_at"] <= time.time():
            get_state().browser_sessions.pop(session_id, None)
            raise HTTPException(status_code=410, detail={"code": "session_expired"})
        return {"success": True, **_public_session(session_id, metadata)}

    @router.post("/sessions/{session_id}/commands")
    async def run_command(
        session_id: str,
        req: BrowserSessionCommandRequest,
        auth=Depends(verify_api_key),
    ):
        state = get_state()
        metadata = state.browser_sessions.get(session_id)
        if not metadata:
            raise HTTPException(status_code=404, detail={"code": "session_not_found"})
        if metadata["expires_at"] <= time.time():
            state.browser_sessions.pop(session_id, None)
            raise HTTPException(status_code=410, detail={"code": "session_expired"})
        payload = _command_payload(session_id, req)
        try:
            response = await daemon_command(
                payload,
                timeout=float(req.timeout_s + 15),
            )
        except Exception as exc:
            lease = metadata.get("proxy_lease")
            if lease and proxy_failure_likely(message=str(exc)):
                lease.report_failure(str(exc))
            if "SessionNotFound" in str(exc):
                state.browser_sessions.pop(session_id, None)
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "browser_command_failed",
                    "message": _safe_daemon_error(exc),
                    "layer": "starsearch",
                    "retryable": "SessionNotFound" not in str(exc),
                },
            ) from exc
        now = time.time()
        metadata["last_active_at"] = now
        metadata["expires_at"] = now + metadata["max_idle_s"]
        lease = metadata.get("proxy_lease")
        if lease and req.command == BrowserCommand.NAVIGATE:
            lease.report_success()
        return {
            "success": True,
            "id": session_id,
            "command": req.command.value,
            "result": response.get("result"),
            "security": response.get("security") or {},
        }

    @router.delete("/sessions/{session_id}")
    async def close_session(session_id: str, auth=Depends(verify_api_key)):
        metadata = get_state().browser_sessions.pop(session_id, None)
        if not metadata:
            return {"success": True, "id": session_id, "status": "already_closed"}
        warning = None
        try:
            await daemon_command({"v": 1, "cmd": "close_session", "sid": session_id})
            lease = metadata.get("proxy_lease")
            if lease:
                lease.report_success()
        except Exception as exc:
            warning = _safe_daemon_error(exc)
        result = {"success": True, "id": session_id, "status": "closed"}
        if warning:
            result["warning"] = warning
        return result

    return router
