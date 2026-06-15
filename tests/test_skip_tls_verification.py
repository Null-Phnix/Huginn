"""
Tests for the skip_tls_verification / ignore_https_errors feature.

Firecrawl parity: POST /v1/probe accepts `skipTlsVerification: bool`.
When true, the browser context is created with `ignore_https_errors: True`
so self-signed certs and broken CA chains work.

Huginn historically hardcoded `ignore_https_errors: True` in new_context()
(line 364 of browser.py pre-Nüwa-pass). That made the feature "always on"
without an API. The Nüwa pass made it explicit, request-controllable, and
default-on (matches Firecrawl's actual default, preserves the historical
Huginn behavior for any existing user).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from huginn.models import ScrapeRequest


class TestScrapeRequestTlsField:
    """The request model must accept and round-trip skip_tls_verification."""

    def test_default_is_true(self):
        """Default is True: matches Firecrawl's default and Huginn's
        historical hardcoded behavior. Self-signed certs and broken CA
        chains have always worked in Huginn; this keeps that working."""
        req = ScrapeRequest(url="https://example.com")
        assert req.skip_tls_verification is True

    def test_explicit_false_accepted(self):
        req = ScrapeRequest(url="https://example.com", skip_tls_verification=False)
        assert req.skip_tls_verification is False

    def test_explicit_true_accepted(self):
        req = ScrapeRequest(url="https://example.com", skip_tls_verification=True)
        assert req.skip_tls_verification is True

    def test_alias_skipTlsVerification_accepted(self):
        """Firecrawl uses camelCase `skipTlsVerification` in their JSON
        wire format. We accept both snake_case and camelCase so a
        Firecrawl-porting user can paste their request verbatim."""
        req = ScrapeRequest(**{"url": "https://example.com", "skipTlsVerification": False})
        assert req.skip_tls_verification is False


class TestBrowserContextTlsFlag:
    """BrowserManager.new_context must respect the TLS flag."""

    @pytest.fixture
    def mock_browser_mgr(self):
        from huginn.browser import BrowserManager
        mgr = BrowserManager()
        # AsyncMock browser + context that handle await + set_default_*
        mock_ctx = AsyncMock()
        mock_ctx.set_default_navigation_timeout = MagicMock()
        mock_ctx.set_default_timeout = MagicMock()
        mock_ctx.add_init_script = AsyncMock()
        mgr._browser = MagicMock()
        mgr._browser.new_context = AsyncMock(return_value=mock_ctx)
        return mgr

    @pytest.mark.asyncio
    async def test_new_context_ignore_https_errors_default_true(self, mock_browser_mgr):
        """Default behavior is ignore_https_errors=True (preserves
        Huginn's historical hardcoded setting + matches Firecrawl's
        actual default)."""
        await mock_browser_mgr.new_context()
        kwargs = mock_browser_mgr._browser.new_context.await_args.kwargs
        assert kwargs["ignore_https_errors"] is True

    @pytest.mark.asyncio
    async def test_new_context_ignore_https_errors_can_be_disabled(self, mock_browser_mgr):
        await mock_browser_mgr.new_context(ignore_https_errors=False)
        kwargs = mock_browser_mgr._browser.new_context.await_args.kwargs
        assert kwargs["ignore_https_errors"] is False
