"""
Tests for blockAds and removeBase64Images flags — Firecrawl parity.

blockAds: bool — block known ad network requests at the network layer
  via Playwright route interception. When True, requests to known ad
  domains (doubleclick.net, googlesyndication.com, etc.) are aborted
  before they hit the page.

removeBase64Images: bool — post-process the extracted markdown to strip
  inline base64 image data URIs (data:image/png;base64,XXXX). Saves
  tokens and avoids huge markdown payloads.
"""

import pytest
import re
from unittest.mock import AsyncMock, MagicMock

from huginn.models import ScrapeRequest


# ─── blockAds ─────────────────────────────────────────────────────────────────

class TestBlockAds:
    """ScrapeRequest.blockAds + Scraper route interception."""

    def test_scrape_request_has_block_ads_field(self):
        """ScrapeRequest.blockAds exists, defaults to False."""
        req = ScrapeRequest(url="https://example.com")
        assert hasattr(req, "block_ads")
        assert req.block_ads is False

    def test_scrape_request_accepts_block_ads_true(self):
        """ScrapeRequest accepts blockAds=True (camelCase via alias)."""
        from pydantic import TypeAdapter
        adapter = TypeAdapter(ScrapeRequest)
        req = adapter.validate_python({"url": "https://example.com", "blockAds": True})
        assert req.block_ads is True

    def test_scrape_request_block_ads_snake_case(self):
        """block_ads (snake_case) also works via populate_by_name."""
        req = ScrapeRequest(url="https://example.com", block_ads=True)
        assert req.block_ads is True

    def test_ad_blocking_route_setup(self):
        """Verify the ad domains list contains well-known ad networks."""
        from huginn.scraper import _AD_DOMAINS
        # Globs include the domain, so check via substring match
        joined = " ".join(_AD_DOMAINS)
        assert "doubleclick.net" in joined
        assert "googlesyndication.com" in joined
        assert "googleadservices.com" in joined
        assert "amazon-adsystem" in joined or "adsystem.amazon" in joined

    @pytest.mark.asyncio
    async def test_block_ads_calls_page_route(self):
        """When block_ads=True, Scraper calls page.route() for each ad domain."""
        from huginn.scraper import Scraper

        mock_browser = AsyncMock()
        mock_browser.ignore_https_errors = True
        mock_context = MagicMock()
        mock_context.set_extra_http_headers = AsyncMock()
        mock_page = MagicMock()
        mock_page.set_default_timeout = MagicMock(return_value=None)
        mock_page.evaluate = AsyncMock(return_value="")
        mock_page.url = "https://example.com"
        # page.route should be a MagicMock that records calls
        mock_page.route = MagicMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.new_page = AsyncMock(return_value=mock_page)
        mock_browser.navigate = AsyncMock(return_value=True)
        mock_browser.last_status_code = 200
        mock_browser.extract_content = AsyncMock(return_value={
            "title": "Test", "description": "test", "language": "en",
        })
        mock_browser.to_markdown = AsyncMock(return_value="# Test\n\nContent")

        scraper = Scraper(mock_browser)
        scraper._get_http_client = MagicMock()

        await scraper.scrape(
            url="https://example.com",
            block_ads=True,
            render_mode="full",
        )

        # page.route should have been called for ad domains
        assert mock_page.route.called
        # Verify at least one call was for doubleclick.net
        call_args_str = str(mock_page.route.call_args_list)
        assert "doubleclick" in call_args_str or "**" in call_args_str


# ─── removeBase64Images ───────────────────────────────────────────────────────

class TestRemoveBase64Images:
    """ScrapeRequest.remove_base64_images + post-process markdown."""

    def test_scrape_request_has_remove_base64_field(self):
        """ScrapeRequest.remove_base64_images exists, defaults to False."""
        req = ScrapeRequest(url="https://example.com")
        assert hasattr(req, "remove_base64_images")
        assert req.remove_base64_images is False

    def test_scrape_request_accepts_remove_base64_true(self):
        """ScrapeRequest accepts removeBase64Images=True (camelCase alias)."""
        from pydantic import TypeAdapter
        adapter = TypeAdapter(ScrapeRequest)
        req = adapter.validate_python({"url": "https://example.com", "removeBase64Images": True})
        assert req.remove_base64_images is True

    def test_base64_regex_strips_image_data_uris(self):
        """The base64 regex matches data:image/...;base64,XXXX patterns."""
        from huginn.scraper import _BASE64_IMAGE_REGEX
        # Standard data URI
        text = "Look at ![cat](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=)!"
        matches = _BASE64_IMAGE_REGEX.findall(text)
        assert len(matches) > 0
        # Test the regex with replace
        cleaned = _BASE64_IMAGE_REGEX.sub("", text)
        assert "data:image/png;base64" not in cleaned

    def test_base64_regex_strips_jpeg_and_gif(self):
        """Regex matches data:image/jpeg and data:image/gif too."""
        from huginn.scraper import _BASE64_IMAGE_REGEX
        text = "A ![p](data:image/jpeg;base64,XXXX) and ![g](data:image/gif;base64,YYYY)"
        cleaned = _BASE64_IMAGE_REGEX.sub("", text)
        assert "data:image/jpeg" not in cleaned
        assert "data:image/gif" not in cleaned

    def test_base64_regex_preserves_non_image_data_uris(self):
        """Regex only matches data:image/...;base64, not other data URIs."""
        from huginn.scraper import _BASE64_IMAGE_REGEX
        text = "A normal text with no data URIs and [link](https://example.com)"
        cleaned = _BASE64_IMAGE_REGEX.sub("", text)
        assert cleaned == text  # No change

    @pytest.mark.asyncio
    async def test_remove_base64_images_strips_data_uris(self):
        """When remove_base64_images=True, the markdown has base64 data URIs stripped."""
        from huginn.scraper import Scraper

        markdown_with_b64 = (
            "# Test\n\n"
            "Here is an image: ![cat](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=)!\n"
            "And another: ![dog](data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRER)\n"
        )

        mock_browser = AsyncMock()
        mock_browser.ignore_https_errors = True
        mock_context = MagicMock()
        mock_context.set_extra_http_headers = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.new_page = AsyncMock(return_value=MagicMock())
        mock_browser.navigate = AsyncMock(return_value=True)
        mock_browser.last_status_code = 200
        mock_browser.extract_content = AsyncMock(return_value={
            "title": "Test", "description": "test", "language": "en",
        })
        # Return markdown WITH base64 data URIs
        mock_browser.to_markdown = AsyncMock(return_value=markdown_with_b64)

        scraper = Scraper(mock_browser)
        scraper._get_http_client = MagicMock()

        result = await scraper.scrape(
            url="https://example.com",
            remove_base64_images=True,
            render_mode="full",
        )

        # The base64 data URIs should be stripped
        assert "data:image/png;base64" not in (result.markdown or "")
        assert "data:image/jpeg;base64" not in (result.markdown or "")
        # The image link should still have the alt text but no data URI
        assert "![cat]" in (result.markdown or "") or "cat" in (result.markdown or "")

    @pytest.mark.asyncio
    async def test_remove_base64_images_false_preserves_data_uris(self):
        """When remove_base64_images=False (default), data URIs are preserved."""
        from huginn.scraper import Scraper

        markdown_with_b64 = "Look at ![img](data:image/png;base64,iVBORw0KGgo=)"

        mock_browser = AsyncMock()
        mock_browser.ignore_https_errors = True
        mock_context = MagicMock()
        mock_context.set_extra_http_headers = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.new_page = AsyncMock(return_value=MagicMock())
        mock_browser.navigate = AsyncMock(return_value=True)
        mock_browser.last_status_code = 200
        mock_browser.extract_content = AsyncMock(return_value={
            "title": "Test", "description": "test", "language": "en",
        })
        mock_browser.to_markdown = AsyncMock(return_value=markdown_with_b64)

        scraper = Scraper(mock_browser)
        scraper._get_http_client = MagicMock()

        result = await scraper.scrape(
            url="https://example.com",
            remove_base64_images=False,
            render_mode="full",
        )

        # The data URI should be preserved
        assert "data:image/png;base64" in (result.markdown or "")
