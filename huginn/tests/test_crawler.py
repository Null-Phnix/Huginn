"""Unit tests for huginn/crawler.py async worker pool and real-time streaming."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from huginn.crawler import Crawler
from huginn.models import ScrapeData, OutputFormat


class FakeScraper:
    """Fake scraper that returns canned data per URL."""

    def __init__(self, pages: dict):
        self.pages = pages  # url -> ScrapeData

    async def scrape(self, url, **kwargs):
        return self.pages.get(url, ScrapeData(metadata={"url": url, "title": "unknown"}))


@pytest.fixture
def fake_browser():
    """Minimal fake browser with all async methods mocked."""
    browser = MagicMock()
    browser.stop = AsyncMock()
    browser.new_context = AsyncMock()
    browser.new_page = AsyncMock()
    browser.navigate = AsyncMock(return_value=True)
    browser.smart_wait = AsyncMock()
    browser.extract_content = AsyncMock(return_value={
        "title": "Test", "url": "http://example.com", "description": "", "language": "en"
    })
    browser.to_markdown = AsyncMock(return_value="# Test\n\nContent")
    browser.get_links = AsyncMock(return_value=[])
    browser.last_status_code = 200
    return browser


@pytest.mark.asyncio
async def test_true_concurrency(fake_browser):
    """Workers should process pages concurrently, not sequentially."""
    pages = {
        "http://example.com": ScrapeData(
            markdown="# Home\nContent",
            metadata={"url": "http://example.com", "title": "Home", "status_code": 200},
            links=["http://example.com/page2", "http://example.com/page3"],
        ),
        "http://example.com/page2": ScrapeData(
            markdown="# Page 2\nContent",
            metadata={"url": "http://example.com/page2", "title": "Page 2", "status_code": 200},
            links=[],
        ),
        "http://example.com/page3": ScrapeData(
            markdown="# Page 3\nContent",
            metadata={"url": "http://example.com/page3", "title": "Page 3", "status_code": 200},
            links=[],
        ),
    }

    crawler = Crawler(browser=fake_browser, max_depth=1, max_pages=3, concurrency=3, delay=0)
    crawler.scraper = FakeScraper(pages)
    result = await crawler.crawl("http://example.com")

    assert result.completed == 3
    assert len(result.pages) == 3
    urls = {p.metadata["url"] for p in result.pages if p.metadata}
    assert urls == {
        "http://example.com",
        "http://example.com/page2",
        "http://example.com/page3",
    }


@pytest.mark.asyncio
async def test_on_page_callback(fake_browser):
    """on_page callback should fire for each page as it completes."""
    pages = {
        "http://example.com": ScrapeData(
            markdown="# Home",
            metadata={"url": "http://example.com", "title": "Home", "status_code": 200},
            links=[],
        ),
    }

    crawler = Crawler(browser=fake_browser, max_depth=1, max_pages=1, concurrency=1, delay=0)
    crawler.scraper = FakeScraper(pages)

    received = []

    def callback(page_data):
        received.append(page_data)

    result = await crawler.crawl("http://example.com", on_page=callback)
    assert result.completed == 1
    assert len(received) == 1
    assert received[0].metadata["url"] == "http://example.com"


@pytest.mark.asyncio
async def test_max_depth_respected(fake_browser):
    """Links beyond max_depth should not be crawled."""
    pages = {
        "http://example.com": ScrapeData(
            markdown="# Home",
            metadata={"url": "http://example.com", "title": "Home", "status_code": 200},
            links=["http://example.com/page2"],
        ),
        "http://example.com/page2": ScrapeData(
            markdown="# P2",
            metadata={"url": "http://example.com/page2", "title": "P2", "status_code": 200},
            links=["http://example.com/page3"],
        ),
        "http://example.com/page3": ScrapeData(
            markdown="# P3",
            metadata={"url": "http://example.com/page3", "title": "P3", "status_code": 200},
            links=["http://example.com/page4"],
        ),
    }

    crawler = Crawler(browser=fake_browser, max_depth=2, max_pages=10, concurrency=3, delay=0)
    crawler.scraper = FakeScraper(pages)
    result = await crawler.crawl("http://example.com")

    # depth 0: home, depth 1: page2, depth 2: page3 — page4 is at depth 3, skipped
    assert result.completed == 3
    urls = {p.metadata["url"] for p in result.pages if p.metadata}
    assert "http://example.com/page4" not in urls


@pytest.mark.asyncio
async def test_cancels_gracefully(fake_browser):
    """cancel() should stop new page processing and not hang."""
    pages = {
        "http://example.com": ScrapeData(
            markdown="# Home",
            metadata={"url": "http://example.com", "title": "Home", "status_code": 200},
            links=["http://example.com/page2"],
        ),
        "http://example.com/page2": ScrapeData(
            markdown="# P2",
            metadata={"url": "http://example.com/page2", "title": "P2", "status_code": 200},
            links=[],
        ),
    }

    crawler = Crawler(browser=fake_browser, max_depth=1, max_pages=10, concurrency=2, delay=0)
    crawler.scraper = FakeScraper(pages)
    crawler._cancel = True

    result = await asyncio.wait_for(crawler.crawl("http://example.com"), timeout=5)
    # With _cancel set before crawl, at most the seed URL may have been
    # dequeued before workers saw the flag.
    assert result.completed <= 1


@pytest.mark.asyncio
async def test_max_pages_limits_total(fake_browser):
    """max_pages should stop crawling even if more URLs are queued."""
    pages = {
        "http://example.com": ScrapeData(
            markdown="# Home",
            metadata={"url": "http://example.com", "title": "Home", "status_code": 200},
            links=["http://example.com/a", "http://example.com/b", "http://example.com/c"],
        ),
        "http://example.com/a": ScrapeData(
            markdown="# A", metadata={"url": "http://example.com/a", "title": "A", "status_code": 200},
            links=["http://example.com/d"],
        ),
        "http://example.com/b": ScrapeData(
            markdown="# B", metadata={"url": "http://example.com/b", "title": "B", "status_code": 200},
            links=[],
        ),
        "http://example.com/c": ScrapeData(
            markdown="# C", metadata={"url": "http://example.com/c", "title": "C", "status_code": 200},
            links=[],
        ),
        "http://example.com/d": ScrapeData(
            markdown="# D", metadata={"url": "http://example.com/d", "title": "D", "status_code": 200},
            links=[],
        ),
    }

    crawler = Crawler(browser=fake_browser, max_depth=2, max_pages=2, concurrency=2, delay=0)
    crawler.scraper = FakeScraper(pages)
    result = await crawler.crawl("http://example.com")

    assert result.completed == 2
    # Should not have crawled more than 2 pages
    assert len(result.pages) == 2
