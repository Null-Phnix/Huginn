"""Huginn Webhooks — fire callbacks when jobs complete.

No polling. Huginn calls YOUR endpoint when sweep/distill/flock finishes.

Usage:
    # Add webhook to any job request
    result = await client.sweep(url="https://example.com", webhook_url="https://myapp.com/hook")

    # Your endpoint receives:
    # {
    #   "event": "job.completed",      # or "job.failed"
    #   "job_id": "uuid",
    #   "job_type": "sweep",
    #   "status": "completed",        # or "failed"
    #   "success": true,
    #   "data": { ... },              # result summary (not full data)
    #   "error": null,                # present on failure
    #   "completed_at": "2026-04-18T..."
    # }
"""

import asyncio
import hashlib
import hmac
import json
import logging
import socket
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult

from .security import ResolvedPublicTarget, resolve_public_url_target

logger = logging.getLogger(__name__)

DEFAULT_WEBHOOK_TIMEOUT = 10.0  # seconds
DEFAULT_MAX_RETRIES = 3
RETRY_DELAYS = [1, 3, 10]  # seconds between retries


class _PinnedResolver(AbstractResolver):
    """Resolve one approved hostname only to its prevalidated addresses."""

    def __init__(self, target: ResolvedPublicTarget):
        self._target = target

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[ResolveResult]:
        normalized = host.rstrip(".").encode("idna").decode("ascii").lower()
        if normalized != self._target.hostname or port != self._target.port:
            raise OSError("Refusing to resolve an unvalidated webhook target")

        results = [
            ResolveResult(
                hostname=host,
                host=address,
                port=port,
                family=address_family,
                proto=socket.IPPROTO_TCP,
                flags=socket.AI_NUMERICHOST,
            )
            for address_family, address in self._target.addresses
            if family in {socket.AF_UNSPEC, address_family}
        ]
        if not results:
            raise OSError("Validated webhook target has no address for this socket family")
        return results

    async def close(self) -> None:
        return None


def _target_label(url: str) -> str:
    """Return a log-safe target label without credentials, query, or fragment."""
    try:
        return urlparse(url).hostname or "invalid target"
    except ValueError:
        return "invalid target"


async def _deliver_webhook(
    *,
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout: float,
) -> None:
    """Deliver through one DNS-pinned public target without redirects."""
    target = await resolve_public_url_target(url)
    connector = aiohttp.TCPConnector(
        resolver=_PinnedResolver(target),
        family=socket.AF_UNSPEC,
        use_dns_cache=True,
        ttl_dns_cache=None,
    )
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=client_timeout,
        trust_env=False,
    ) as client:
        async with client.post(
            url,
            data=body,
            headers=headers,
            allow_redirects=False,
        ) as response:
            response.raise_for_status()


def _compute_signature(body: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 signature of `body` using `secret`.

    Returns a 64-char hex digest. The caller is responsible for
    prefixing with `sha256=` when assembling the header value.

    Receiver verification (Firecrawl parity):
        body = await request.body()  # raw bytes
        expected = hmac.new(secret.encode(), body, sha256).hexdigest()
        assert request.headers["X-Huginn-Signature"] == f"sha256={expected}"
    """
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def send_webhook(
    url: str,
    payload: dict,
    secret: Optional[str] = None,
    timeout: float = DEFAULT_WEBHOOK_TIMEOUT,
) -> bool:
    """Send a webhook POST with optional HMAC-SHA256 signature.

    When `secret` is set:
      - Computes HMAC-SHA256 over the JSON-serialized body
      - Adds `X-Huginn-Signature: sha256=<hex>` header
      - Also sends the legacy `X-Huginn-Webhook-Secret` header for
        backward compatibility with endpoints that pre-date signatures

    Returns True on 2xx response, False on any failure.
    """
    if not url:
        return False

    # Serialize the body once — used for both HMAC and the HTTP POST
    body_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Huginn-Webhook/1.0",
    }
    if secret:
        sig = _compute_signature(body_bytes, secret)
        headers["X-Huginn-Signature"] = f"sha256={sig}"
        # Legacy header — kept for backward compat
        headers["X-Huginn-Webhook-Secret"] = secret

    try:
        await _deliver_webhook(
            url=url,
            body=body_bytes,
            headers=headers,
            timeout=timeout,
        )
        logger.info("Webhook delivered to %s", _target_label(url))
        return True
    except Exception as e:
        logger.warning("Webhook failed to %s: %s", _target_label(url), type(e).__name__)
        return False


async def send_webhook_with_retry(
    url: str,
    payload: dict,
    secret: Optional[str] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout: float = DEFAULT_WEBHOOK_TIMEOUT,
) -> bool:
    """Send a webhook with exponential backoff retries."""
    for attempt in range(max_retries + 1):
        if await send_webhook(url, payload, secret=secret, timeout=timeout):
            return True
        if attempt < max_retries:
            delay = RETRY_DELAYS[attempt] if attempt < len(RETRY_DELAYS) else RETRY_DELAYS[-1]
            logger.info(
                "Retrying webhook to %s in %ss (attempt %s/%s)",
                _target_label(url),
                delay,
                attempt + 2,
                max_retries + 1,
            )
            await asyncio.sleep(delay)
    logger.error(
        "Webhook failed after %s attempts: %s",
        max_retries + 1,
        _target_label(url),
    )
    return False


async def fire_webhook_for_job(
    webhook_url: str,
    job_id: str,
    job_type: str,
    status: str,
    success: bool,
    data: Any = None,
    error: Optional[str] = None,
    secret: Optional[str] = None,
) -> None:
    """Fire a webhook for a job completion/failure.

    This is fire-and-forget — errors are logged but do not propagate.
    Webhook delivery failures should never crash the job handler.
    """
    if not webhook_url:
        return

    event = "job.completed" if status in ("completed", "cancelled") else "job.failed"

    payload = {
        "event": event,
        "job_id": job_id,
        "job_type": job_type,
        "status": status,
        "success": success,
        "data": data,
        "error": error,
    }

    try:
        await send_webhook_with_retry(webhook_url, payload, secret=secret)
    except Exception as e:
        # Never let webhook failures affect the calling code
        logger.error(f"Unexpected error firing webhook: {e}")
