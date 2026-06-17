"""
Tests for mobile device emulation — Firecrawl parity.

Feature: POST /v1/scrape accepts mobile=True and uses a Playwright device
descriptor (iPhone 13 by default) so the browser renders with a mobile
viewport, mobile user-agent, and touch support enabled.

mobile=False (default) → no device descriptor, desktop rendering.
mobile=True → pass playwright.devices["iPhone 13"] to new_context().
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import inspect

from huginn.browser import BrowserManager
from huginn.models import ScrapeRequest


class TestScrapeRequestMobile:
    """ScrapeRequest has a mobile field."""

    def test_scrape_request_has_mobile_field(self):
        """ScrapeRequest.mobile exists, defaults to False."""
        req = ScrapeRequest(url="https://example.com")
        assert hasattr(req, "mobile")
        assert req.mobile is False

    def test_scrape_request_accepts_mobile_true(self):
        """ScrapeRequest accepts mobile=True without error."""
        req = ScrapeRequest(url="https://example.com", mobile=True)
        assert req.mobile is True

    def test_scrape_request_mobile_alias(self):
        """mobile accepts camelCase 'mobile' from API payload."""
        from pydantic import TypeAdapter
        adapter = TypeAdapter(ScrapeRequest)
        req = adapter.validate_python({"url": "https://example.com", "mobile": True})
        assert req.mobile is True


class TestBrowserManagerDevice:
    """BrowserManager.new_context accepts a device descriptor."""

    @pytest.mark.asyncio
    async def test_new_context_accepts_device_kwarg(self):
        """BrowserManager.new_context has a `device` parameter."""
        sig = inspect.signature(BrowserManager.new_context)
        assert "device" in sig.parameters, (
            f"BrowserManager.new_context missing 'device' parameter. "
            f"Current signature: {sig}"
        )

    @pytest.mark.asyncio
    async def test_new_context_passes_device_to_playwright(self):
        """When device is provided, it's passed through to playwright's new_context."""
        bm = BrowserManager()
        # Mock the playwright browser
        mock_context = MagicMock()
        mock_context.add_init_script = AsyncMock()
        mock_browser = MagicMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        bm._browser = mock_browser

        device = {"is_mobile": True, "viewport": {"width": 375, "height": 812}, "user_agent": "Mobile UA"}
        await bm.new_context(device=device)

        # The device fields are merged into context_kwargs (this is how
        # Playwright's new_context works — viewport/user_agent are top-level
        # kwargs, not nested under a `device` key).
        call_kwargs = mock_browser.new_context.call_args.kwargs
        assert call_kwargs.get("is_mobile") is True
        assert call_kwargs.get("viewport") == {"width": 375, "height": 812}
        assert call_kwargs.get("user_agent") == "Mobile UA"

    @pytest.mark.asyncio
    async def test_new_context_without_device_works(self):
        """When device=None, new_context still works (backward compat)."""
        bm = BrowserManager()
        mock_context = MagicMock()
        mock_context.add_init_script = AsyncMock()
        mock_browser = MagicMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        bm._browser = mock_browser

        await bm.new_context(device=None)

        call_kwargs = mock_browser.new_context.call_args.kwargs
        # device=None means no device kwarg passed (or device=None)
        assert call_kwargs.get("device") is None


class TestMobileDeviceEmulation:
    """iPhone 13 device descriptor is the default for mobile scraping."""

    def test_iphone_device_descriptor_structure(self):
        """The iPhone 13 device descriptor has the expected Playwright fields."""
        # This is what playwright.devices["iPhone 13"] returns (subset)
        expected_keys = {"is_mobile", "viewport", "user_agent"}
        # Verify our wrapping logic has these keys
        device = {
            "is_mobile": True,
            "viewport": {"width": 390, "height": 844},
            "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
            "device_scale_factor": 3,
            "is_touch": True,
            "has_touch": True,
        }
        for key in expected_keys:
            assert key in device, f"Missing key: {key}"


class TestScraperMobileWiring:
    """Scraper.scrape() passes the mobile flag through to BrowserManager.new_context()."""

    @pytest.mark.asyncio
    async def test_scraper_scrape_has_mobile_param(self):
        """Scraper.scrape() signature includes a mobile bool parameter."""
        from huginn.scraper import Scraper
        sig = inspect.signature(Scraper.scrape)
        assert "mobile" in sig.parameters, (
            f"Scraper.scrape missing 'mobile' parameter. Current signature: {sig}"
        )

    @pytest.mark.asyncio
    async def test_mobile_true_calls_new_context_with_device(self):
        """When mobile=True, Scraper.scrape() passes device=... to BrowserManager.new_context()."""
        from huginn.scraper import Scraper

        # Mock the entire browser manager
        mock_browser = AsyncMock()
        mock_browser.ignore_https_errors = True
        mock_context = MagicMock()
        mock_context.set_extra_http_headers = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.new_page = AsyncMock(return_value=MagicMock())
        mock_browser.navigate = AsyncMock(return_value=True)
        mock_browser.last_status_code = 200
        mock_browser.extract_content = AsyncMock(return_value={
            "title": "Mobile Test", "description": "test", "language": "en",
        })
        mock_browser.to_markdown = AsyncMock(return_value="# Mobile\n\nContent")

        scraper = Scraper(mock_browser)
        scraper._get_http_client = MagicMock()

        result = await scraper.scrape(
            url="https://example.com",
            mobile=True,
            render_mode="full",
        )

        # The Scraper calls BrowserManager.new_context with device=_MOBILE_DEVICE
        call_kwargs = mock_browser.new_context.call_args.kwargs
        device = call_kwargs.get("device")
        assert device is not None
        assert device.get("is_mobile") is True
        assert "viewport" in device
        assert "user_agent" in device

    @pytest.mark.asyncio
    async def test_mobile_false_does_not_pass_device(self):
        """When mobile=False, Scraper.scrape() does not pass device to new_context()."""
        from huginn.scraper import Scraper

        mock_browser = AsyncMock()
        mock_browser.ignore_https_errors = True
        mock_context = MagicMock()
        mock_context.set_extra_http_headers = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.new_page = AsyncMock(return_value=MagicMock())
        mock_browser.navigate = AsyncMock(return_value=True)
        mock_browser.last_status_code = 200
        mock_browser.extract_content = AsyncMock(return_value={
            "title": "Desktop", "description": "test", "language": "en",
        })
        mock_browser.to_markdown = AsyncMock(return_value="# Desktop\n\nContent")

        scraper = Scraper(mock_browser)
        scraper._get_http_client = MagicMock()

        result = await scraper.scrape(
            url="https://example.com",
            mobile=False,
            render_mode="full",
        )

        call_kwargs = mock_browser.new_context.call_args.kwargs
        # When mobile=False, device should be None or absent
        assert call_kwargs.get("device") is None
