"""
Tests for the public Huginn Python SDK (sdk/python/huginn_client/).

These tests verify the public API surface — anything a downstream
`pip install huginn-client` user would interact with. The internal
huginn/sdk.py is being phased out in favor of this public SDK.
"""

import asyncio
import os
import sys

# Ensure the published SDK is importable from the source tree.
_sdk_path = os.path.join(os.path.dirname(__file__), "..", "sdk", "python")
if os.path.isdir(_sdk_path) and _sdk_path not in sys.path:
    sys.path.insert(0, _sdk_path)

import httpx
import pytest

from huginn_client import (
    CircuitOpenError,
    HuginnClient,
    HuginnError,
    HuginnSync,
    JobCancelledError,
    JobNotFoundError,
    RateLimitError,
)


class TestHuginnClientInit:
    """HuginnClient constructor + attribute storage."""

    def test_default_base_url(self):
        client = HuginnClient()
        assert client.base_url == "http://localhost:7432"

    def test_api_key_from_env(self, monkeypatch):
        """HUGINN_API_KEY env var is picked up when api_key=None."""
        monkeypatch.setenv("HUGINN_API_KEY", "sk-test-123")
        client = HuginnClient()
        assert client._api_key == "sk-test-123"

    def test_api_key_explicit(self):
        """Explicit api_key wins over env var."""
        client = HuginnClient(api_key="sk-explicit")
        assert client._api_key == "sk-explicit"

    def test_explicit_key_overrides_env(self, monkeypatch):
        """Explicit api_key takes precedence over HUGINN_API_KEY."""
        monkeypatch.setenv("HUGINN_API_KEY", "sk-from-env")
        client = HuginnClient(api_key="sk-explicit")
        assert client._api_key == "sk-explicit"

    def test_client_has_timeout(self):
        """Client stores a timeout (default 60s)."""
        client = HuginnClient(timeout=30.0)
        assert client.timeout == 30.0

    def test_client_custom_base_url(self):
        """Custom base_url is stored (trailing slash stripped)."""
        client = HuginnClient(base_url="http://example.com:9000/")
        assert client.base_url == "http://example.com:9000"

    def test_auth_headers_empty_when_no_key(self):
        """No api_key + no env -> no Authorization header."""
        client = HuginnClient(api_key="")
        assert "Authorization" not in client._auth_headers()

    def test_auth_headers_set_when_key_provided(self):
        """api_key -> Authorization: Bearer header is set."""
        client = HuginnClient(api_key="sk-test")
        headers = client._auth_headers()
        assert headers["Authorization"] == "Bearer sk-test"


class TestHuginnErrorHierarchy:
    """HuginnError + subclass hierarchy."""

    def test_error_basic(self):
        """HuginnError captures message."""
        err = HuginnError("test message")
        assert str(err) == "test message"

    def test_error_status_code_default_none(self):
        """HuginnError.status_code defaults to None (not 0)."""
        err = HuginnError("test")
        assert err.status_code is None

    def test_error_with_status_code(self):
        """HuginnError stores status_code when provided."""
        err = HuginnError("not found", status_code=404)
        assert err.status_code == 404

    def test_circuit_open_error_inherits(self):
        """CircuitOpenError is a HuginnError."""
        err = CircuitOpenError("circuit open")
        assert isinstance(err, HuginnError)

    def test_rate_limit_error_inherits(self):
        """RateLimitError is a HuginnError."""
        err = RateLimitError("rate limit")
        assert isinstance(err, HuginnError)

    def test_job_not_found_error_inherits(self):
        """JobNotFoundError is a HuginnError."""
        err = JobNotFoundError("not found")
        assert isinstance(err, HuginnError)

    def test_job_cancelled_error_inherits(self):
        """JobCancelledError is a HuginnError."""
        err = JobCancelledError("cancelled")
        assert isinstance(err, HuginnError)


class TestHuginnSync:
    """HuginnSync is the sync shim over HuginnClient."""

    def test_huginn_sync_constructor(self):
        """HuginnSync takes the same params as HuginnClient."""
        sync = HuginnSync(base_url="http://example.com", api_key="sk-test")
        assert sync._async is not None
        assert sync._async.base_url == "http://example.com"
        assert sync._async._api_key == "sk-test"

    def test_huginn_sync_has_close(self):
        """HuginnSync has a close() method."""
        sync = HuginnSync()
        assert hasattr(sync, "close")
        assert callable(sync.close)


class TestSDKExports:
    """The public SDK __all__ exports."""

    def test_all_contains_expected_exports(self):
        """__all__ includes HuginnClient, HuginnSync, and the error classes."""
        import huginn_client
        for name in (
            "HuginnClient",
            "HuginnError",
            "HuginnSync",
            "CircuitOpenError",
            "RateLimitError",
            "JobNotFoundError",
            "JobCancelledError",
        ):
            assert name in huginn_client.__all__, f"{name} missing from __all__"

    def test_version_is_set(self):
        """__version__ is set (so pip show works)."""
        import huginn_client
        assert huginn_client.__version__ != "0.0.0"
        parts = huginn_client.__version__.split(".")
        assert len(parts) >= 2
        for p in parts:
            assert p.isdigit()


# ─── CircuitOpenError / RateLimitError edge cases ──────────────────────────

class TestCircuitAndRateLimitErrors:
    """CircuitOpenError + RateLimitError — extra coverage beyond inheritance."""

    def test_circuit_open_error_message_preserved(self):
        err = CircuitOpenError("circuit open for example.com")
        assert "example.com" in str(err)

    def test_circuit_open_error_can_carry_status_code(self):
        err = CircuitOpenError("cb open", status_code=503)
        assert err.status_code == 503

    def test_circuit_open_error_can_carry_error_code(self):
        err = CircuitOpenError("cb open", error_code="circuit_open")
        assert err.error_code == "circuit_open"

    def test_circuit_open_error_can_be_raised_and_caught(self):
        with pytest.raises(CircuitOpenError):
            raise CircuitOpenError("test")
        with pytest.raises(HuginnError):
            raise CircuitOpenError("test")

    def test_rate_limit_error_message_preserved(self):
        err = RateLimitError("429: too many requests")
        assert "429" in str(err)

    def test_rate_limit_error_status_code_429(self):
        err = RateLimitError("rate limited", status_code=429)
        assert err.status_code == 429

    def test_rate_limit_error_response_attachment(self):
        resp = httpx.Response(429, json={"error": "rate limited"})
        err = RateLimitError("limited", response=resp)
        assert err.response is resp
        assert err.response.status_code == 429


# ─── HuginnSync methods via MockTransport ───────────────────────────────────

class TestHuginnSyncMethods:
    """HuginnSync wraps the async client via asyncio.run()."""

    def test_huginn_sync_has_health(self):
        sync = HuginnSync()
        assert hasattr(sync, "health")
        assert callable(sync.health)

    def test_huginn_sync_has_scrape(self):
        sync = HuginnSync()
        assert hasattr(sync, "scrape")
        assert callable(sync.scrape)

    def test_huginn_sync_has_probe(self):
        sync = HuginnSync()
        assert hasattr(sync, "probe")
        assert callable(sync.probe)

    def test_huginn_sync_has_sweep_start(self):
        sync = HuginnSync()
        assert hasattr(sync, "sweep_start")
        assert callable(sync.sweep_start)

    def test_huginn_sync_has_chart(self):
        sync = HuginnSync()
        assert hasattr(sync, "chart")
        assert callable(sync.chart)

    def test_huginn_sync_has_flock(self):
        sync = HuginnSync()
        assert hasattr(sync, "flock")
        assert callable(sync.flock)

    def test_huginn_sync_has_distill_start(self):
        sync = HuginnSync()
        assert hasattr(sync, "distill_start")
        assert callable(sync.distill_start)

    def test_huginn_sync_close_without_context_no_error(self):
        """Closing HuginnSync without entering async context is a safe no-op.

        `_run` auto-enters the async context via __aenter__ before
        calling close, so close() never raises even when the caller
        never used `with sync:` or `async with sync:`.
        """
        sync = HuginnSync()
        # No `with sync:` was called — _async._client is None
        # close() should not raise (handled by _run's auto-context)
        sync.close()

    def test_huginn_sync_health_calls_health_endpoint(self):
        """HuginnSync.health() makes a GET to /health."""
        captured = {}

        def handler(request):
            captured["path"] = request.url.path
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, json={"status": "ok", "uptime_s": 42.0})

        sync = HuginnSync(base_url="http://test:7432", api_key="sk-test")
        original_client = httpx.AsyncClient

        def patched_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return original_client(*args, **kwargs)

        httpx.AsyncClient = patched_client
        try:
            result = sync.health()
        finally:
            httpx.AsyncClient = original_client

        assert captured["path"] == "/health"
        assert result == {"status": "ok", "uptime_s": 42.0}

    def test_huginn_sync_scrape_posts_to_v1_probe(self):
        """HuginnSync.scrape() posts to /v1/probe with the URL + auth."""
        captured = {}

        def handler(request):
            captured["method"] = request.method
            captured["path"] = request.url.path
            captured["body"] = request.read().decode("utf-8")
            captured["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json={
                "success": True,
                "data": {"markdown": "# Page", "metadata": {"url": "https://example.com"}},
            })

        sync = HuginnSync(base_url="http://test:7432", api_key="sk-test")
        original_client = httpx.AsyncClient

        def patched_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return original_client(*args, **kwargs)

        httpx.AsyncClient = patched_client
        try:
            result = sync.scrape("https://example.com")
        finally:
            httpx.AsyncClient = original_client

        assert captured["method"] == "POST"
        assert captured["path"] == "/v1/probe"
        assert "https://example.com" in captured["body"]
        assert captured["auth"] == "Bearer sk-test"
        assert result.success is True
        assert result.data.markdown == "# Page"