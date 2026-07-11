"""
Huginn shared utilities — helpers used across routers and tasks.

Extracted from api.py to keep routers focused on route definitions.
"""

import json
import logging
from typing import Any, Optional

from fastapi import Header, HTTPException

from .config import HuginnConfig
from .models import ErrorCode, ScrapeData, ScrapeOptions
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


def scrape_failure(data: Optional[ScrapeData]) -> Optional[tuple[int, str]]:
    """Return ``(status, message)`` when a scraper result represents failure.

    Scraper backends intentionally return structured ``ScrapeData`` for several
    terminal failures instead of raising. Routers, crawlers, and caches must not
    mistake that transport-level return for a successful page.
    """
    if data is None:
        return 500, "Scraper returned no data"
    metadata = data.metadata or {}
    try:
        status = int(metadata.get("status_code", 200))
    except (TypeError, ValueError):
        status = 500
    if status >= 400:
        return status, str(metadata.get("error") or f"Upstream returned HTTP {status}")
    return None


def scrape_options_kwargs(options: Optional[ScrapeOptions]) -> dict[str, Any]:
    """Translate public ScrapeOptions into the internal Scraper call contract."""
    if options is None:
        return {}
    return {
        "headers": options.headers,
        "wait_for": options.wait_for,
        "actions": [action.model_dump(mode="json", exclude_none=True) for action in options.actions]
        if options.actions else None,
        "include_tags": options.include_tags,
        "exclude_tags": options.exclude_tags,
        "only_main_content": options.only_main_content,
        "timeout": options.timeout,
        "max_retries": options.max_retries,
        "scroll": options.scroll,
        "render_mode": options.render_mode,
        "skip_tls_verification": options.skip_tls_verification,
        "mobile": options.mobile,
        "block_ads": options.block_ads,
        "remove_base64_images": options.remove_base64_images,
        "change_tracking": options.change_tracking,
        "cookies": options.cookies,
        "location": options.location.model_dump(exclude_none=True)
        if options.location else None,
    }


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
