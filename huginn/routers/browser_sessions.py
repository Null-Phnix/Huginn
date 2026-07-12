"""Authenticated, local Browserbase-style session lifecycle over StarSearch."""

from __future__ import annotations

import asyncio
import re
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..config import HuginnConfig
from ..models import (
    BrowserCommand,
    BrowserContextDeleteResponse,
    BrowserContextListResponse,
    BrowserSessionCloseResponse,
    BrowserSessionCommandRequest,
    BrowserSessionCommandResponse,
    BrowserSessionCreateRequest,
    BrowserSessionListResponse,
    BrowserSessionResponse,
)
from ..proxy import ProxyConfigurationError, ProxyEndpoint, ProxyUnavailable
from ..starsearch_scrape import daemon_command, daemon_status
from ..state import get_state
from ..utils import get_proxy_provider, proxy_failure_likely

_CONTEXT_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

# A successful session launch, DOM interaction, or close says nothing about the
# configured proxy's ability to reach the network.  `navigate` is currently the
# only public command whose successful daemon response proves an egress request
# completed through the session's proxy.


def _proves_proxy_egress(req: BrowserSessionCommandRequest) -> bool:
    if req.command is not BrowserCommand.NAVIGATE or not req.url:
        return False
    try:
        return urllib.parse.urlsplit(req.url).scheme.lower() in {"http", "https"}
    except ValueError:
        return False


def _timestamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _public_session(session_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
    result = {
        "id": session_id,
        "status": metadata.get("status", "active"),
        "created_at": _timestamp(metadata["created_at"]),
        "last_active_at": _timestamp(metadata["last_active_at"]),
        "expires_at": _timestamp(metadata["expires_at"]),
        "locale": metadata["locale"],
        "human_level": metadata["human_level"],
        "allowed_domains": metadata["allowed_domains"],
        "allow_internal_network": metadata["allow_internal_network"],
        "allow_evaluate": metadata.get("allow_evaluate", False),
        "allow_cookie_access": metadata.get("allow_cookie_access", False),
        "proxy_configured": metadata["proxy_configured"],
        "daemon_instance_id": metadata.get("daemon_instance_id"),
    }
    if metadata.get("context"):
        result["context"] = metadata["context"]
    if metadata.get("warning"):
        result["warning"] = metadata["warning"]
    return result


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


def _require_context_auth(config: HuginnConfig) -> None:
    """Persistent browser data is unavailable on an anonymously configured API."""
    if not config.server.api_key:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "persistent_context_auth_required",
                "message": (
                    "Configure HUGINN_API_KEY or HUGINN_API_KEY_FILE before using "
                    "persistent browser contexts"
                ),
                "layer": "huginn-security",
                "retryable": False,
            },
        )


def _validate_context_name(name: str) -> str:
    if not _CONTEXT_NAME.fullmatch(name):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_context_name",
                "message": (
                    "Context names must be 1-64 characters, begin with a lowercase "
                    "letter or digit, and contain only lowercase letters, digits, '.', '_', or '-'"
                ),
                "layer": "huginn",
                "retryable": False,
            },
        )
    return name


def _daemon_context_error(exc: Exception) -> HTTPException:
    message = _safe_daemon_error(exc)
    mappings = (
        ("InvalidContextName", 422, "invalid_context_name", False),
        ("ContextNotFound", 404, "context_not_found", False),
        ("ContextAlreadyExists", 409, "context_already_exists", False),
        ("ContextInUse", 409, "context_in_use", True),
        ("ContextQuarantined", 409, "context_quarantined", False),
        ("ContextConfigMismatch", 409, "context_config_mismatch", False),
        ("ContextCorrupt", 500, "context_corrupt", False),
        ("ContextStoreUnsafe", 503, "context_store_unsafe", False),
        ("ContextStoreIO", 503, "context_store_io", True),
    )
    for category, status, code, retryable in mappings:
        if category in message:
            return HTTPException(
                status_code=status,
                detail={
                    "code": code,
                    "message": message,
                    "layer": "starsearch-context-store",
                    "retryable": retryable,
                },
            )
    return HTTPException(
        status_code=502,
        detail={
            "code": "browser_context_operation_failed",
            "message": message,
            "layer": "starsearch",
            "retryable": True,
        },
    )


def _context_timestamp(value: Any) -> Any:
    return _timestamp(float(value)) if isinstance(value, (int, float)) else value


def _public_context(raw: dict[str, Any]) -> dict[str, Any]:
    required = {
        "name",
        "created_at",
        "last_opened_at",
        "locale",
        "profile_persistent",
        "runtime_restart_survival",
        "scope",
        "active",
    }
    if not isinstance(raw, dict) or not required.issubset(raw):
        raise ValueError("context summary is missing required fields")
    if (
        raw["profile_persistent"] is not True
        or raw["runtime_restart_survival"] is not False
        or raw["scope"] != "host"
        or not isinstance(raw["active"], bool)
    ):
        raise ValueError("context summary has unsupported persistence semantics")
    return {
        "context_id": raw["name"],
        "name": raw["name"],
        "created_at": _context_timestamp(raw["created_at"]),
        "last_opened_at": _context_timestamp(raw["last_opened_at"]),
        "locale": raw["locale"],
        "profile_persistent": raw["profile_persistent"],
        "runtime_restart_survival": raw["runtime_restart_survival"],
        "scope": raw["scope"],
        "active": raw["active"],
        "active_session_id": raw.get("active_session_id"),
        "quarantined": bool(raw.get("quarantined", False)),
    }


def _public_session_context(raw: Any, requested_name: str) -> dict[str, Any]:
    required = {
        "name",
        "persistent",
        "created",
        "scope",
        "profile_persistent",
        "runtime_restart_survival",
    }
    if not isinstance(raw, dict) or not required.issubset(raw):
        raise ValueError("named session response is missing context fields")
    if (
        raw["name"] != requested_name
        or raw["persistent"] is not True
        or not isinstance(raw["created"], bool)
        or raw["scope"] != "host"
        or raw["profile_persistent"] is not True
        or raw["runtime_restart_survival"] is not False
    ):
        raise ValueError("named session response has unsupported persistence semantics")
    return {
        "context_id": raw["name"],
        "id": raw["name"],
        "persistent": raw["persistent"],
        "created": raw["created"],
        "scope": raw["scope"],
        "profile_persistent": raw["profile_persistent"],
        "runtime_restart_survival": raw["runtime_restart_survival"],
    }


def _reconcile_runtime(metadata: dict[str, Any], daemon: dict[str, Any]) -> str:
    """Update one local handle from the daemon's authoritative runtime id."""
    expected = metadata.get("daemon_instance_id")
    if not expected:
        return metadata.get("status", "active")
    if not daemon.get("reachable"):
        metadata["status"] = "runtime_unreachable"
        metadata["warning"] = str(daemon.get("error") or "StarSearch is unreachable")[:1000]
        return "runtime_unreachable"
    current = daemon.get("instance_id")
    if expected and current and expected != current:
        metadata["status"] = "interrupted"
        metadata["warning"] = (
            "StarSearch restarted; the live browser session no longer exists. "
            "Create a new session; any named context profile remains available."
        )
        return "interrupted"
    if metadata.get("status") == "runtime_unreachable":
        metadata["status"] = "active"
        metadata.pop("warning", None)
    return metadata.get("status", "active")


async def _close_session_under_lock(
    state, session_id: str, metadata: dict[str, Any]
) -> tuple[str, list[str]]:
    """Close at the daemon before forgetting the only local retry handle."""
    try:
        response = await daemon_command({"v": 1, "cmd": "close_session", "sid": session_id})
        warnings = (response.get("result") or {}).get("warnings") or []
        warnings = [str(warning)[:1000] for warning in warnings if warning]
        status = "closed_with_warnings" if warnings else "closed"
    except Exception as exc:
        if "SessionNotFound" not in str(exc):
            metadata["status"] = "close_failed"
            metadata["warning"] = _safe_daemon_error(exc)
            raise
        status = "already_closed"
        warnings = []
    if state.browser_sessions.get(session_id) is metadata:
        state.browser_sessions.pop(session_id, None)
    return status, warnings


async def _close_tracked_session(state, session_id: str) -> tuple[str, list[str]]:
    metadata = state.browser_sessions.get(session_id)
    if not metadata:
        return "already_closed", []
    lock = state.browser_session_locks.setdefault(session_id, asyncio.Lock())
    try:
        async with lock:
            metadata = state.browser_sessions.get(session_id)
            if not metadata:
                return "already_closed", []
            return await _close_session_under_lock(state, session_id, metadata)
    finally:
        if session_id not in state.browser_sessions:
            state.browser_session_locks.pop(session_id, None)


async def close_all_browser_sessions(timeout_s: float = 10.0) -> dict[str, int]:
    """Best-effort graceful drain used by Huginn shutdown.

    Named StarSearch contexts keep their Chromium profile after session close, while
    the runtime session id remains intentionally ephemeral.
    """
    state = get_state()
    closed = 0
    failed = 0
    for session_id in list(state.browser_sessions):
        try:
            await asyncio.wait_for(_close_tracked_session(state, session_id), timeout=timeout_s)
            closed += 1
        except Exception:
            failed += 1
    return {"closed": closed, "failed": failed}


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

    @router.post("/sessions", response_model=BrowserSessionResponse)
    async def create_session(
        req: BrowserSessionCreateRequest,
        auth=Depends(verify_api_key),
    ):
        del auth
        if not req.context_id and "context_mode" in req.model_fields_set:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "context_id_required",
                    "message": "context_mode requires context_id",
                    "layer": "huginn",
                    "retryable": False,
                },
            )
        if req.context_id:
            _require_context_auth(config)
            _validate_context_name(req.context_id)
            if req.proxy:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "context_proxy_override_not_allowed",
                        "message": (
                            "Named contexts use the server proxy provider; per-request raw proxy "
                            "credentials are not accepted"
                        ),
                        "layer": "huginn-egress",
                        "retryable": False,
                    },
                )
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
                lease_key = (
                    f"browser-context:{req.context_id}"
                    if req.context_id
                    else f"browser:{uuid.uuid4()}"
                )
                proxy_lease = get_proxy_provider(config).acquire(
                    session_key=lease_key,
                    strict_sticky=bool(req.context_id),
                )
                proxy_payload = proxy_lease.as_browser_proxy()
            opts: dict[str, Any] = {
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
                    "allow_evaluate": req.allow_evaluate,
                    "allow_cookie_access": req.allow_cookie_access,
                    "max_idle_s": req.max_idle_s,
                },
            }
            if req.context_id:
                opts["context"] = {
                    "name": req.context_id,
                    "mode": req.context_mode.value,
                }
            response = await daemon_command(
                {"v": 1, "cmd": "new_session", "sid": None, "opts": opts}
            )
        except (ProxyConfigurationError, ProxyUnavailable) as exc:
            raise HTTPException(
                status_code=422 if isinstance(exc, ProxyConfigurationError) else 503,
                detail={
                    "code": "invalid_proxy"
                    if isinstance(exc, ProxyConfigurationError)
                    else "proxy_unavailable",
                    "message": _safe_daemon_error(exc, req.proxy),
                    "layer": "huginn-egress",
                    "retryable": isinstance(exc, ProxyUnavailable),
                },
            ) from exc
        except Exception as exc:
            if proxy_lease and proxy_failure_likely(message=str(exc)):
                proxy_lease.report_failure(str(exc))
            if req.context_id and "Context" in str(exc):
                raise _daemon_context_error(exc) from exc
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
        daemon_result = response.get("result") or {}
        session_id = response.get("sid") or daemon_result.get("sid")
        daemon_instance_id = daemon_result.get("daemon_instance_id")
        if not session_id:
            raise HTTPException(
                status_code=502,
                detail={"code": "invalid_daemon_response", "message": "No session id returned"},
            )
        if not isinstance(daemon_instance_id, str) or len(daemon_instance_id) != 32:
            try:
                await daemon_command({"v": 1, "cmd": "close_session", "sid": session_id})
            except Exception:
                pass
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "invalid_daemon_response",
                    "message": "No valid daemon runtime identity returned",
                    "layer": "starsearch",
                },
            )
        context = daemon_result.get("context")
        public_context = None
        try:
            if req.context_id:
                public_context = _public_session_context(context, req.context_id)
            elif context is not None:
                raise ValueError("ephemeral session unexpectedly returned context metadata")
        except ValueError as exc:
            try:
                await daemon_command({"v": 1, "cmd": "close_session", "sid": session_id})
            except Exception:
                pass
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "invalid_daemon_response",
                    "message": str(exc),
                    "layer": "starsearch",
                },
            ) from exc
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
            "allow_evaluate": req.allow_evaluate,
            "allow_cookie_access": req.allow_cookie_access,
            "proxy_configured": bool(proxy_payload),
            "proxy_lease": proxy_lease,
            "context": public_context,
            "status": "active",
            "daemon_instance_id": daemon_instance_id,
        }
        state = get_state()
        state.browser_sessions[session_id] = metadata
        state.browser_session_locks[session_id] = asyncio.Lock()
        return {"success": True, **_public_session(session_id, metadata)}

    @router.get("/sessions", response_model=BrowserSessionListResponse)
    async def list_sessions(auth=Depends(verify_api_key)):
        del auth
        state = get_state()
        daemon = await daemon_status()
        for metadata in state.browser_sessions.values():
            _reconcile_runtime(metadata, daemon)
        now = time.time()
        expired = [
            sid
            for sid, metadata in list(state.browser_sessions.items())
            if metadata["expires_at"] <= now
        ]
        for sid in expired:
            try:
                await _close_tracked_session(state, sid)
            except Exception:
                # Keep failed closes visible and retryable instead of dropping the lease handle.
                pass
        return {
            "success": True,
            "sessions": [
                _public_session(sid, metadata) for sid, metadata in state.browser_sessions.items()
            ],
            "daemon": daemon,
        }

    @router.get("/sessions/{session_id}", response_model=BrowserSessionResponse)
    async def get_session(session_id: str, auth=Depends(verify_api_key)):
        del auth
        state = get_state()
        metadata = state.browser_sessions.get(session_id)
        if not metadata:
            raise HTTPException(status_code=404, detail={"code": "session_not_found"})
        _reconcile_runtime(metadata, await daemon_status())
        if metadata["expires_at"] <= time.time():
            try:
                await _close_tracked_session(state, session_id)
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "session_expiry_cleanup_failed",
                        "message": _safe_daemon_error(exc),
                        "layer": "starsearch",
                        "retryable": True,
                    },
                ) from exc
            raise HTTPException(status_code=410, detail={"code": "session_expired"})
        return {"success": True, **_public_session(session_id, metadata)}

    @router.post(
        "/sessions/{session_id}/commands",
        response_model=BrowserSessionCommandResponse,
    )
    async def run_command(
        session_id: str,
        req: BrowserSessionCommandRequest,
        auth=Depends(verify_api_key),
    ):
        del auth
        state = get_state()
        metadata = state.browser_sessions.get(session_id)
        if not metadata:
            raise HTTPException(status_code=404, detail={"code": "session_not_found"})
        lock = state.browser_session_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            metadata = state.browser_sessions.get(session_id)
            if not metadata:
                raise HTTPException(status_code=404, detail={"code": "session_not_found"})
            if metadata["expires_at"] <= time.time():
                try:
                    await _close_session_under_lock(state, session_id, metadata)
                except Exception as exc:
                    raise HTTPException(
                        status_code=502,
                        detail={
                            "code": "session_expiry_cleanup_failed",
                            "message": _safe_daemon_error(exc),
                            "layer": "starsearch",
                            "retryable": True,
                        },
                    ) from exc
                state.browser_session_locks.pop(session_id, None)
                raise HTTPException(status_code=410, detail={"code": "session_expired"})
            runtime_status = _reconcile_runtime(metadata, await daemon_status())
            if runtime_status == "interrupted":
                raise HTTPException(
                    status_code=410,
                    detail={
                        "code": "browser_session_interrupted",
                        "message": metadata["warning"],
                        "layer": "starsearch",
                        "retryable": False,
                    },
                )
            if runtime_status == "runtime_unreachable":
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "browser_runtime_unreachable",
                        "message": metadata["warning"],
                        "layer": "starsearch",
                        "retryable": True,
                    },
                )
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
                    state.browser_session_locks.pop(session_id, None)
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
            metadata["status"] = "active"
            metadata.pop("warning", None)
            lease = metadata.get("proxy_lease")
            if lease and _proves_proxy_egress(req):
                lease.report_success()
            return {
                "success": True,
                "id": session_id,
                "command": req.command.value,
                "result": response.get("result"),
                "security": response.get("security") or {},
            }

    @router.delete("/sessions/{session_id}", response_model=BrowserSessionCloseResponse)
    async def close_session(session_id: str, auth=Depends(verify_api_key)):
        del auth
        state = get_state()
        if session_id not in state.browser_sessions:
            response: dict[str, Any] = {}
            try:
                response = await daemon_command({"v": 1, "cmd": "close_session", "sid": session_id})
            except Exception as exc:
                if "SessionNotFound" not in str(exc):
                    raise HTTPException(
                        status_code=502,
                        detail={
                            "code": "browser_session_close_failed",
                            "message": _safe_daemon_error(exc),
                            "layer": "starsearch",
                            "retryable": True,
                        },
                    ) from exc
            daemon_result = response.get("result") or {}
            warnings = daemon_result.get("warnings") or []
            if daemon_result.get("already_closed") is True:
                status = "already_closed"
            elif warnings:
                status = "closed_with_warnings"
            else:
                status = "closed"
            return {
                "success": True,
                "id": session_id,
                "status": status,
                "warnings": warnings,
            }
        try:
            status, warnings = await _close_tracked_session(state, session_id)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "browser_session_close_failed",
                    "message": _safe_daemon_error(exc),
                    "layer": "starsearch",
                    "retryable": True,
                },
            ) from exc
        return {
            "success": True,
            "id": session_id,
            "status": status,
            "warnings": warnings,
        }

    @router.get("/contexts", response_model=BrowserContextListResponse)
    async def list_contexts(auth=Depends(verify_api_key)):
        del auth
        _require_context_auth(config)
        try:
            response = await daemon_command({"v": 1, "cmd": "list_contexts"})
        except Exception as exc:
            raise _daemon_context_error(exc) from exc
        contexts = (response.get("result") or {}).get("contexts")
        if not isinstance(contexts, list):
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "invalid_daemon_response",
                    "message": "Context list response did not include contexts",
                    "layer": "starsearch",
                },
            )
        try:
            public_contexts = [_public_context(item) for item in contexts]
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "invalid_daemon_response",
                    "message": str(exc),
                    "layer": "starsearch",
                },
            ) from exc
        return {"success": True, "contexts": public_contexts}

    @router.delete("/contexts/{context_id}", response_model=BrowserContextDeleteResponse)
    async def delete_context(context_id: str, auth=Depends(verify_api_key)):
        del auth
        _require_context_auth(config)
        _validate_context_name(context_id)
        try:
            response = await daemon_command(
                {"v": 1, "cmd": "delete_context", "name": context_id}
            )
        except Exception as exc:
            raise _daemon_context_error(exc) from exc
        result = response.get("result") or {}
        if result.get("name") != context_id or result.get("deleted") is not True:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "invalid_daemon_response",
                    "message": "Context delete response did not confirm deletion",
                    "layer": "starsearch",
                },
            )
        return {
            "success": True,
            "context_id": context_id,
            "name": context_id,
            "status": "deleted",
        }

    return router
