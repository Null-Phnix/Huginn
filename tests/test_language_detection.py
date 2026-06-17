"""
Tests for metadata.language detection — Firecrawl parity.

Feature: POST /v1/scrape returns metadata.language indicating the detected
or declared language of the scraped page.

Two detection strategies:
  1. HTML <html lang="..."> attribute (declarative — most pages set this)
  2. Character-frequency n-gram fallback when no lang attribute is present

The metadata dict always includes a "language" key (default: "en").
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from huginn.scraper import Scraper
from huginn.models import OutputFormat


class TestLanguageDetection:
    """Language is extracted from page metadata or detected from content."""

    @pytest.mark.asyncio
    async def test_metadata_includes_language_key(self):
        """ScrapeData.metadata always has a 'language' key (default: en)."""
        mock_browser = AsyncMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_page.set_default_timeout = MagicMock(return_value=None)
        mock_page.url = "https://example.com"
        mock_page.evaluate = AsyncMock(return_value="")  # PDF check
        mock_page.evaluate_innerHTML = AsyncMock(return_value="<html><body>content</body></html>")
        mock_page.inner_html = "<html><body>content</body></html>"
        mock_page.content = AsyncMock(return_value="<html><body>content</body></html>")
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.new_page = AsyncMock(return_value=mock_page)
        mock_browser.navigate = AsyncMock(return_value=True)
        mock_browser.last_status_code = 200
        mock_browser.extract_content = AsyncMock(return_value={
            "title": "Test",
            "description": "test page",
            "language": "en",
        })
        mock_browser.to_markdown = AsyncMock(return_value="# Test\n\nPage content.")

        scraper = Scraper(mock_browser)
        scraper._get_http_client = MagicMock()

        result = await scraper.scrape(
            url="https://example.com",
            formats=[OutputFormat.MARKDOWN],
            render_mode="full",
        )

        assert result.metadata is not None
        assert result.metadata.get("language") == "en"

    @pytest.mark.asyncio
    async def test_language_from_lang_attribute(self):
        """When <html lang="fr"> is set, metadata.language is 'fr'."""
        mock_browser = AsyncMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_page.set_default_timeout = MagicMock(return_value=None)
        mock_page.url = "https://example.com/fr/page"
        mock_page.evaluate = AsyncMock(return_value="")
        mock_page.evaluate_innerHTML = AsyncMock(return_value='<html lang="fr"><body>Contenu français</body></html>')
        mock_page.inner_html = '<html lang="fr"><body>Contenu français</body></html>'
        mock_page.content = AsyncMock(return_value='<html lang="fr"><body>Contenu français</body></html>')
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.new_page = AsyncMock(return_value=mock_page)
        mock_browser.navigate = AsyncMock(return_value=True)
        mock_browser.last_status_code = 200
        mock_browser.extract_content = AsyncMock(return_value={
            "title": "Page française",
            "description": "une page en français",
            "language": "fr",
        })
        mock_browser.to_markdown = AsyncMock(return_value="# Page française\n\nContenu français")

        scraper = Scraper(mock_browser)
        scraper._get_http_client = MagicMock()

        result = await scraper.scrape(
            url="https://example.com/fr/page",
            formats=[OutputFormat.MARKDOWN],
            render_mode="full",
        )

        assert result.metadata.get("language") == "fr"

    @pytest.mark.asyncio
    async def test_language_defaults_to_en_when_not_set(self):
        """When page has no lang attribute, metadata.language defaults to 'en'."""
        mock_browser = AsyncMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_page.set_default_timeout = MagicMock(return_value=None)
        mock_page.url = "https://example.com"
        mock_page.evaluate = AsyncMock(return_value="")
        mock_page.evaluate_innerHTML = AsyncMock(return_value="<html><body>Some content</body></html>")
        mock_page.inner_html = "<html><body>Some content</body></html>"
        mock_page.content = AsyncMock(return_value="<html><body>Some content</body></html>")
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.new_page = AsyncMock(return_value=mock_page)
        mock_browser.navigate = AsyncMock(return_value=True)
        mock_browser.last_status_code = 200
        mock_browser.extract_content = AsyncMock(return_value={
            "title": "Page",
            "description": "content",
            "language": "en",  # browser defaults to en when not set
        })
        mock_browser.to_markdown = AsyncMock(return_value="# Page\n\nSome content")

        scraper = Scraper(mock_browser)
        scraper._get_http_client = MagicMock()

        result = await scraper.scrape(
            url="https://example.com",
            formats=[OutputFormat.MARKDOWN],
            render_mode="full",
        )

        assert result.metadata["language"] == "en"

    @pytest.mark.asyncio
    async def test_lightweight_scrape_also_includes_language(self):
        """Language detection works in the lightweight (httpx) path too."""
        mock_browser = AsyncMock()
        mock_browser.extract_content = AsyncMock(return_value={
            "title": "DE Seite",
            "description": "Deutsche Seite",
            "language": "de",
        })
        mock_browser.to_markdown = AsyncMock(return_value="# DE Seite\n\nDeutsche Seite")

        mock_browser.last_status_code = 200

        # lightweight_scrape uses httpx, not playwright
        scraper = Scraper(mock_browser)
        scraper._get_http_client = MagicMock()

        # Mock the lightweight path to return a result with language
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.text = '<html lang="de"><body>Deutsche Inhalt</body></html>'
        mock_response.url = "https://example.com/de"

        mock_client = AsyncMock()
        mock_client.head = AsyncMock(return_value=mock_response)
        mock_client.get = AsyncMock(return_value=mock_response)
        scraper._get_http_client = MagicMock(return_value=mock_client)

        result = await scraper.scrape(
            url="https://example.com/de",
            formats=[OutputFormat.MARKDOWN],
            render_mode="auto",
        )

        # Result should have language in metadata from the extraction
        assert result.metadata is not None
        assert "language" in result.metadata
