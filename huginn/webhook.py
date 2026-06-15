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
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_WEBHOOK_TIMEOUT = 10.0  # seconds
DEFAULT_MAX_RETRIES = 3
RETRY_DELAYS = [1, 3, 10]  # seconds between retries


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
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, content=body_bytes, headers=headers)
            resp.raise_for_status()
            logger.info(f"Webhook delivered to {url}")
            return True
    except Exception as e:
        logger.warning(f"Webhook failed to {url}: {e}")
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
            logger.info(f"Retrying webhook to {url} in {delay}s (attempt {attempt + 2}/{max_retries + 1})")
            await asyncio.sleep(delay)
    logger.error(f"Webhook failed after {max_retries + 1} attempts: {url}")
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
