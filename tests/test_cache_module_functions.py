"""
Tests for the module-level cache functions in huginn.cache.

These are thin wrappers around AsyncTTLCache:
  - get_response_cache() — process-wide singleton (in-memory, no I/O)
  - cache_scrape_result() — store a ScrapeData for a (url, formats) pair
  - get_cached_scrape_result() — retrieve a cached ScrapeData
  - invalidate_cache() — remove entries (optionally scoped to a URL)

Note: cache is purely in-memory (AsyncTTLCache), so tests are
deterministic and don't need a temp dir.
"""

import asyncio
import pytest

from huginn.cache import (
    get_response_cache,
    cache_scrape_result,
    get_cached_scrape_result,
    invalidate_cache,
)
from huginn.models import ScrapeData


class TestResponseCacheSingleton:
    """get_response_cache() returns a process-wide singleton."""

    @pytest.mark.asyncio
    async def test_get_response_cache_returns_instance(self):
        """First call creates the cache, returns AsyncTTLCache instance."""
        cache = await get_response_cache()
        assert cache is not None

    @pytest.mark.asyncio
    async def test_get_response_cache_is_singleton(self):
        """Subsequent calls return the same instance."""
        a = await get_response_cache()
        b = await get_response_cache()
        assert a is b


class TestCacheScrapeResult:
    """cache_scrape_result() — store a ScrapeData for a (url, formats) pair."""

    @pytest.mark.asyncio
    async def test_cache_then_retrieve(self):
        """Store a result, retrieve it, verify round-trip."""
        result = ScrapeData(
            markdown="# Page\n\nContent",
            metadata={"url": "https://example.com", "title": "Example"},
        )
        await cache_scrape_result(
            "https://example.com",
            ["markdown"],
            result,
        )
        retrieved = await get_cached_scrape_result("https://example.com", ["markdown"])
        assert retrieved is not None
        assert retrieved.markdown == "# Page\n\nContent"
        assert retrieved.metadata["url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_cache_different_formats_same_url(self):
        """Same URL with different formats → different cache entries."""
        markdown_result = ScrapeData(markdown="# MD")
        html_result = ScrapeData(html="<h1>HTML</h1>")

        await cache_scrape_result("https://example.com", ["markdown"], markdown_result)
        await cache_scrape_result("https://example.com", ["html"], html_result)

        md = await get_cached_scrape_result("https://example.com", ["markdown"])
        html = await get_cached_scrape_result("https://example.com", ["html"])

        assert md is not None
        assert html is not None
        assert md.markdown == "# MD"
        assert md.html is None
        assert html.html == "<h1>HTML</h1>"
        assert html.markdown is None

    @pytest.mark.asyncio
    async def test_get_cached_returns_none_for_miss(self):
        """get_cached_scrape_result returns None when nothing is cached."""
        result = await get_cached_scrape_result("https://never-cached.com", ["markdown"])
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_options_do_not_collide(self):
        """The same URL with different rendering options must not share data."""
        await cache_scrape_result(
            "https://options.example",
            ["markdown"],
            ScrapeData(markdown="desktop"),
            extra={"mobile": False},
        )
        assert await get_cached_scrape_result(
            "https://options.example", ["markdown"], extra={"mobile": True}
        ) is None
        cached = await get_cached_scrape_result(
            "https://options.example", ["markdown"], extra={"mobile": False}
        )
        assert cached is not None
        assert cached.markdown == "desktop"


class TestInvalidateCache:
    """invalidate_cache() — remove entries (optionally scoped to a URL)."""

    @pytest.mark.asyncio
    async def test_invalidate_specific_url(self):
        """invalidate_cache(url=X) removes only X's cache entries."""
        result_a = ScrapeData(markdown="A")
        result_b = ScrapeData(markdown="B")

        await cache_scrape_result("https://a.com", ["markdown"], result_a)
        await cache_scrape_result("https://b.com", ["markdown"], result_b)

        # Invalidate only a.com
        await invalidate_cache(url="https://a.com")

        # a.com is gone
        assert await get_cached_scrape_result("https://a.com", ["markdown"]) is None
        # b.com is still there
        assert await get_cached_scrape_result("https://b.com", ["markdown"]) is not None

    @pytest.mark.asyncio
    async def test_invalidate_all(self):
        """invalidate_cache() with no url clears everything."""
        await cache_scrape_result("https://a.com", ["markdown"], ScrapeData(markdown="A"))
        await cache_scrape_result("https://b.com", ["markdown"], ScrapeData(markdown="B"))

        await invalidate_cache()

        assert await get_cached_scrape_result("https://a.com", ["markdown"]) is None
        assert await get_cached_scrape_result("https://b.com", ["markdown"]) is None
