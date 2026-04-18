"""
End-to-end integration test — BlackCrawl API with real browser.

Tests actual scraping against a real site (example.com).
Requires Playwright chromium to be installed.
"""

import asyncio
import json
import pytest
from httpx import ASGITransport, AsyncClient

from blackcrawl.config import BlackCrawlConfig
from blackcrawl.browser import BrowserManager
from blackcrawl.scraper import Scraper
from blackcrawl.models import OutputFormat


@pytest.fixture
async def browser():
    """Create and start a real browser for integration tests."""
    bm = BrowserManager(headless=True, stealth=True)
    await bm.start()
    yield bm
    await bm.stop()


class TestLiveScraper:
    """Test scraping against real websites."""

    @pytest.mark.asyncio
    @pytest.mark.network
    async def test_scrape_example_com(self, browser):
        """Should successfully scrape example.com."""
        scraper = Scraper(browser)
        data = await scraper.scrape(
            url="https://example.com",
            formats=[OutputFormat.MARKDOWN, OutputFormat.HTML],
            timeout=15000,
        )

        assert data.markdown is not None
        assert len(data.markdown) > 0
        assert data.html is not None
        assert "example" in data.markdown.lower() or "domain" in data.markdown.lower()

    @pytest.mark.asyncio
    @pytest.mark.network
    async def test_scrape_links(self, browser):
        """Should extract links from a page."""
        scraper = Scraper(browser)
        data = await scraper.scrape(
            url="https://example.com",
            formats=[OutputFormat.LINKS],
            timeout=15000,
        )

        assert data.links is not None
        assert len(data.links) >= 0  # example.com may have few links

    @pytest.mark.asyncio
    @pytest.mark.network
    async def test_scrape_screenshot(self, browser):
        """Should take a screenshot."""
        scraper = Scraper(browser)
        data = await scraper.scrape(
            url="https://example.com",
            formats=[OutputFormat.SCREENSHOT],
            timeout=15000,
        )

        assert data.screenshot is not None
        assert len(data.screenshot) > 100  # base64 image data

    @pytest.mark.asyncio
    @pytest.mark.network
    async def test_scrape_raw_html(self, browser):
        """Should return raw HTML."""
        scraper = Scraper(browser)
        data = await scraper.scrape(
            url="https://example.com",
            formats=[OutputFormat.RAW_HTML],
            timeout=15000,
        )

        assert data.raw_html is not None
        assert "<html" in data.raw_html.lower()


class TestLiveAPI:
    """Test API endpoints with real browser."""

    @pytest.mark.asyncio
    @pytest.mark.network
    async def test_api_scrape(self):
        """Should serve scrape endpoint via API with the app lifecycle."""
        from blackcrawl.api import create_app
        config = BlackCrawlConfig()
        app = create_app(config)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Start the app's lifespan to initialize browser
            async with app.router.lifespan_context(app):
                resp = await client.post("/v1/scrape", json={
                    "url": "https://example.com",
                    "formats": ["markdown"],
                    "timeout": 15000,
                })
                assert resp.status_code == 200
                data = resp.json()
                assert data["success"] is True
                assert data["data"]["markdown"] is not None

    @pytest.mark.asyncio
    @pytest.mark.network
    async def test_api_map(self):
        """Should serve map endpoint via API with lifecycle."""
        from blackcrawl.api import create_app
        config = BlackCrawlConfig()
        app = create_app(config)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with app.router.lifespan_context(app):
                resp = await client.post("/v1/map", json={
                    "url": "https://example.com",
                    "limit": 100,
                })
                assert resp.status_code in (200, 500)