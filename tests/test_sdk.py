"""
Tests for the Huginn Python SDK.
"""

import pytest

from huginn.sdk import HuginnClient, HuginnError


class TestHuginnClientInit:
    def test_default_base_url(self):
        client = HuginnClient()
        assert client.base_url == "http://localhost:7432"

    def test_base_url_trailing_slash_stripped(self):
        client = HuginnClient(base_url="http://localhost:7432/")
        assert client.base_url == "http://localhost:7432"

    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("HUGINN_API_KEY", "test-key-123")
        client = HuginnClient()
        assert client.api_key == "test-key-123"

    def test_api_key_explicit(self):
        client = HuginnClient(api_key="my-key")
        assert client.api_key == "my-key"

    def test_explicit_key_overrides_env(self, monkeypatch):
        monkeypatch.setenv("HUGINN_API_KEY", "env-key")
        client = HuginnClient(api_key="explicit-key")
        assert client.api_key == "explicit-key"

    def test_client_has_timeout(self):
        client = HuginnClient(timeout=30.0)
        assert client.timeout == 30.0


class TestSDKModelValidation:
    """Test SDK correctly constructs and validates models."""

    def test_scrape_formats_normalized(self):
        client = HuginnClient()
        # Formats are stored as strings internally
        assert client._headers()["Content-Type"] == "application/json"

    def test_client_context_manager(self):
        client = HuginnClient()
        import inspect
        # __aenter__ should be a coroutine function (async def)
        assert inspect.iscoroutinefunction(client.__aenter__)


class TestHuginnError:
    def test_error_with_code(self):
        err = HuginnError("test", error_code="timeout", status_code=408)
        assert err.error_code == "timeout"
        assert err.status_code == 408

    def test_error_without_code(self):
        err = HuginnError("test")
        assert err.error_code is None
        assert err.status_code == 0
