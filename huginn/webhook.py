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
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_WEBHOOK_TIMEOUT = 10.0  # seconds
DEFAULT_MAX_RETRIES = 3
RETRY_DELAYS = [1, 3, 10]  # seconds between retries


async def send_webhook(
    url: str,
    payload: dict,
    secret: Optional[str] = None,
    timeout: float = DEFAULT_WEBHOOK_TIMEOUT,
) -> bool:
    """Send a webhook POST. Returns True on success, False on any failure."""
    if not url:
        return False

    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Huginn-Webhook-Secret"] = secret

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
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
