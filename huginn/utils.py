"""
Huginn shared utilities — helpers used across routers and tasks.

Extracted from api.py to keep routers focused on route definitions.
"""

import json
import logging
from typing import Optional

from fastapi import Header, HTTPException

from .config import HuginnConfig
from .models import ErrorCode
from .state import get_state, limiter

logger = logging.getLogger(__name__)

# SSE format helper — avoids f-string issues with newlines
_SSE_TEMPLATE = "event: {}\ndata: {}\n\n"


def sse_event(event: str, data: dict) -> str:
    """Format an SSE event: event line + data line + blank line."""
    return _SSE_TEMPLATE.format(event, json.dumps(data))


def _map_exception_to_error_code(e: Exception) -> Optional[str]:
    """Map exception type to Huginn ErrorCode string."""
    name = type(e).__name__.lower()
    mapping = {
        "timeouterror": ErrorCode.TIMEOUT,
        "httpx.timeout": ErrorCode.TIMEOUT,
        "httpx.connecterror": ErrorCode.CONNECTION_ERROR,
        "circuit openerror": ErrorCode.CIRCUIT_OPEN,
        "valueerror": ErrorCode.INVALID_URL,
        "urllib.error.httperror": ErrorCode.UPSTREAM_ERROR,
        "playwright.timeout": ErrorCode.TIMEOUT,
    }
    for key, code in mapping.items():
        if key in name:
            return code
    return ErrorCode.NAVIGATION_FAILED


def build_proxy_dict(config: HuginnConfig) -> Optional[dict]:
    """Build proxy dict from config for Playwright context."""
    if not config.proxy.server:
        return None
    proxy = {"server": config.proxy.server}
    if config.proxy.username:
        proxy["username"] = config.proxy.username
    if config.proxy.password:
        proxy["password"] = config.proxy.password
    return proxy


def make_verify_api_key(config: HuginnConfig):
    """Create a verify_api_key dependency bound to the app's config."""

    async def verify_api_key(authorization: Optional[str] = Header(None)):
        """Optional API key verification."""
        if not config.server.api_key:
            return True
        if not authorization:
            raise HTTPException(status_code=401, detail="Missing Authorization header")
        token = authorization.removeprefix("Bearer ").strip()
        if token != config.server.api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return True

    return verify_api_key