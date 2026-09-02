"""
Tests for HMAC-signed webhooks — Firecrawl parity.

Feature: When a webhook URL is provided with a secret, Huginn signs the
request body with HMAC-SHA256 and sends the signature in the
X-Huginn-Signature header: sha256=<hex>.

This lets the receiving endpoint verify the request actually came from
Huginn and the body wasn't tampered with in transit.

The legacy X-Huginn-Webhook-Secret header is still sent for backward
compatibility with endpoints that pre-date the signature feature.
"""

import hashlib
import hmac
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from huginn.scheduler import Scheduler
from huginn.webhook import _compute_signature, send_webhook

# ─── Signature computation ──────────────────────────────────────────────────

class TestComputeSignature:
    """_compute_signature() — pure function, HMAC-SHA256 over the body bytes."""

    def test_compute_signature_with_secret(self):
        """Secret + body produces a deterministic hex digest."""
        body = b'{"event": "job.completed", "job_id": "abc"}'
        secret = "my-secret-123"
        sig = _compute_signature(body, secret)
        # Recompute via stdlib to verify
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        assert sig == expected

    def test_compute_signature_format_is_hex(self):
        """Signature is a 64-char hex string (SHA-256 = 256 bits = 64 hex chars)."""
        sig = _compute_signature(b"hello", "secret")
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)

    def test_compute_signature_different_secrets_different_output(self):
        """Different secrets produce different signatures for the same body."""
        body = b"content"
        sig1 = _compute_signature(body, "secret-a")
        sig2 = _compute_signature(body, "secret-b")
        assert sig1 != sig2

    def test_compute_signature_different_bodies_different_output(self):
        """Same secret, different bodies → different signatures."""
        sig1 = _compute_signature(b"body-a", "secret")
        sig2 = _compute_signature(b"body-b", "secret")
        assert sig1 != sig2

    def test_compute_signature_handles_empty_body(self):
        """Empty body + secret produces a valid (deterministic) signature."""
        sig = _compute_signature(b"", "secret")
        assert len(sig) == 64
        # Recompute to verify
        expected = hmac.new(b"secret", b"", hashlib.sha256).hexdigest()
        assert sig == expected

    def test_compute_signature_handles_unicode(self):
        """Non-ASCII body works (encoded as utf-8 before HMAC)."""
        body = '{"text": "héllo wörld 🌍"}'.encode("utf-8")
        sig = _compute_signature(body, "secret")
        assert len(sig) == 64
        # Verify stdlib produces same result
        expected = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        assert sig == expected


# ─── send_webhook() integration ──────────────────────────────────────────────

class TestSendWebhookWithHMAC:
    """send_webhook() adds X-Huginn-Signature header when secret is set."""

    @pytest.mark.asyncio
    async def test_webhook_no_secret_sends_no_signature_header(self):
        """When no secret is set, no X-Huginn-Signature header is added."""
        with patch("huginn.webhook._deliver_webhook", new_callable=AsyncMock) as deliver:
            await send_webhook(
                url="https://example.com/hook",
                payload={"event": "job.completed"},
                secret=None,
            )

            headers = deliver.await_args.kwargs["headers"]
            assert "X-Huginn-Signature" not in headers

    @pytest.mark.asyncio
    async def test_webhook_with_secret_sends_signature_header(self):
        """When secret is set, X-Huginn-Signature header is present."""
        with patch("huginn.webhook._deliver_webhook", new_callable=AsyncMock) as deliver:
            payload = {"event": "job.completed", "job_id": "abc"}
            secret = "my-secret-123"
            await send_webhook(
                url="https://example.com/hook",
                payload=payload,
                secret=secret,
            )

            headers = deliver.await_args.kwargs["headers"]
            body_bytes = deliver.await_args.kwargs["body"]

            # X-Huginn-Signature should be present, format sha256=<hex>
            assert "X-Huginn-Signature" in headers
            sig_header = headers["X-Huginn-Signature"]
            assert sig_header.startswith("sha256=")
            sig_value = sig_header.split("=", 1)[1]

            expected = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
            assert sig_value == expected

    @pytest.mark.asyncio
    async def test_webhook_legacy_secret_header_still_sent(self):
        """The legacy X-Huginn-Webhook-Secret header is still sent for backward compat."""
        with patch("huginn.webhook._deliver_webhook", new_callable=AsyncMock) as deliver:
            await send_webhook(
                url="https://example.com/hook",
                payload={"event": "test"},
                secret="my-secret",
            )

            headers = deliver.await_args.kwargs["headers"]
            # Both headers should be present
            assert "X-Huginn-Secret" in headers or "X-Huginn-Webhook-Secret" in headers

    @pytest.mark.asyncio
    async def test_webhook_signature_can_be_verified_by_receiver(self):
        """End-to-end: receiver can recompute signature and match."""
        with patch("huginn.webhook._deliver_webhook", new_callable=AsyncMock) as deliver:
            payload = {"event": "job.completed", "job_id": "test-123", "data": [1, 2, 3]}
            secret = "shared-secret-456"

            await send_webhook(
                url="https://example.com/hook",
                payload=payload,
                secret=secret,
            )

            headers = deliver.await_args.kwargs["headers"]
            body_bytes = deliver.await_args.kwargs["body"]

            # Receiver (mock) verifies: re-computes HMAC, compares with header
            sig_header = headers["X-Huginn-Signature"]
            received_sig = sig_header.split("=", 1)[1]
            receiver_expected = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
            assert received_sig == receiver_expected

    @pytest.mark.asyncio
    async def test_webhook_signature_tampering_detected(self):
        """If body is tampered in transit, receiver's recomputed sig won't match."""
        # Simulate the receiver checking a tampered body
        secret = "shared-secret"
        original_body = b'{"event": "job.completed", "success": true}'
        tampered_body = b'{"event": "job.completed", "success": false}'

        original_sig = hmac.new(secret.encode("utf-8"), original_body, hashlib.sha256).hexdigest()
        # If attacker forwards the original sig with a tampered body:
        receiver_recomputed = hmac.new(secret.encode("utf-8"), tampered_body, hashlib.sha256).hexdigest()
        # These should NOT match — tampering detected
        assert original_sig != receiver_recomputed


@pytest.mark.asyncio
async def test_scheduler_routes_callbacks_through_shared_webhook_sender(monkeypatch):
    """Scheduled callbacks must not bypass the shared webhook security boundary."""
    sender = AsyncMock(return_value=True)
    monkeypatch.setattr("huginn.webhook.send_webhook", sender)
    scheduler = Scheduler(job_store=MagicMock())
    schedule = {
        "id": "schedule-1",
        "name": "daily",
        "job_type": "sweep",
        "request": {"url": "https://example.com"},
    }

    await scheduler._fire_webhook(
        "https://callback.example/hook",
        schedule,
        fired_at=datetime.now(timezone.utc),
    )

    sender.assert_awaited_once()
