"""
Tests for sitemap: "include|skip|only" on /v1/map — Firecrawl parity.

Feature: POST /v1/map accepts a sitemap field with three modes:
  - "include" (default) — fetch sitemap.xml + crawl page + check sub-sitemaps
  - "skip"              — page crawl only, no sitemap fetch
  - "only"              — sitemap URLs only, no page crawl

The Mapper.map_site() function reads `sitemap` and routes accordingly.
"""

import pytest
from unittest.mock import AsyncMock, patch
from pydantic import TypeAdapter

from huginn.models import MapRequest
from huginn.mapper import Mapper


# ─── MapRequest.sitemap field ─────────────────────────────────────────────────

class TestMapRequestSitemap:
    """MapRequest.sitemap accepts and defaults correctly."""

    def test_map_request_has_sitemap_field(self):
        """MapRequest.sitemap exists, defaults to 'include'."""
        req = MapRequest(url="https://example.com")
        assert hasattr(req, "sitemap")
        assert req.sitemap == "include"

    def test_map_request_accepts_sitemap_skip(self):
        """MapRequest accepts sitemap='skip'."""
        req = MapRequest(url="https://example.com", sitemap="skip")
        assert req.sitemap == "skip"

    def test_map_request_accepts_sitemap_only(self):
        """MapRequest accepts sitemap='only'."""
        req = MapRequest(url="https://example.com", sitemap="only")
        assert req.sitemap == "only"

    def test_map_request_accepts_sitemap_include(self):
        """MapRequest accepts sitemap='include' explicitly."""
        req = MapRequest(url="https://example.com", sitemap="include")
        assert req.sitemap == "include"

    def test_map_request_invalid_sitemap_defaults_to_include_via_mapper(self):
        """Invalid sitemap values are coerced to 'include' by Mapper.map_site()."""
        # MapRequest itself stores any value; the Mapper normalizes it.
        req = MapRequest(url="https://example.com", sitemap="bogus")
        assert req.sitemap == "bogus"  # Model accepts it
        # Mapper.map_site() will fall back to "include" — tested below


# ─── Mapper.map_site() routing ────────────────────────────────────────────────

class TestMapSiteSitemapRouting:
    """Mapper.map_site() respects the sitemap mode and routes to the right strategies."""

    @pytest.mark.asyncio
    async def test_sitemap_include_calls_both_strategies(self):
        """sitemap='include' calls _fetch_sitemap AND _extract_page_links."""
        mapper = Mapper(browser=AsyncMock())
        sitemap_urls = {"https://example.com/a", "https://example.com/b"}
        page_urls = {"https://example.com/c", "https://example.com/d"}

        with patch.object(mapper, "_fetch_sitemap", new=AsyncMock(return_value=sitemap_urls)), \
             patch.object(mapper, "_extract_page_links", new=AsyncMock(return_value=(page_urls, "Title", 200))):
            result = await mapper.map_site("https://example.com", sitemap="include")

        # Both strategies contribute
        assert "https://example.com/a" in result
        assert "https://example.com/b" in result
        assert "https://example.com/c" in result
        assert "https://example.com/d" in result

    @pytest.mark.asyncio
    async def test_sitemap_skip_skips_sitemap_fetch(self):
        """sitemap='skip' does NOT call _fetch_sitemap but DOES call _extract_page_links."""
        mapper = Mapper(browser=AsyncMock())
        page_urls = {"https://example.com/c"}

        sitemap_called = False

        async def _no_sitemap(*args, **kwargs):
            nonlocal sitemap_called
            sitemap_called = True
            return set()

        with patch.object(mapper, "_fetch_sitemap", new=_no_sitemap), \
             patch.object(mapper, "_extract_page_links", new=AsyncMock(return_value=(page_urls, "Title", 200))):
            result = await mapper.map_site("https://example.com", sitemap="skip")

        assert sitemap_called is False, "_fetch_sitemap should not be called when sitemap='skip'"
        # Page links are returned
        assert "https://example.com/c" in result

    @pytest.mark.asyncio
    async def test_sitemap_only_returns_only_sitemap_urls(self):
        """sitemap='only' calls _fetch_sitemap but NOT _extract_page_links."""
        mapper = Mapper(browser=AsyncMock())
        sitemap_urls = {"https://example.com/x", "https://example.com/y"}

        page_extraction_called = False

        async def _no_page(*args, **kwargs):
            nonlocal page_extraction_called
            page_extraction_called = True
            return (set(), None, None)

        with patch.object(mapper, "_fetch_sitemap", new=AsyncMock(return_value=sitemap_urls)), \
             patch.object(mapper, "_extract_page_links", new=_no_page):
            result = await mapper.map_site("https://example.com", sitemap="only")

        assert page_extraction_called is False, "_extract_page_links should not be called when sitemap='only'"
        # Only sitemap URLs are returned
        assert "https://example.com/x" in result
        assert "https://example.com/y" in result
        # No other URLs
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_sitemap_skip_skips_sub_sitemap_check_too(self):
        """sitemap='skip' also skips Strategy 3 (sub-sitemap check) on found URLs."""
        mapper = Mapper(browser=AsyncMock())
        page_urls = {"https://example.com/parent/"}

        # Track calls to _fetch_sitemap
        sitemap_call_count = 0

        async def _counting_sitemap(*args, **kwargs):
            nonlocal sitemap_call_count
            sitemap_call_count += 1
            return set()

        with patch.object(mapper, "_fetch_sitemap", new=_counting_sitemap), \
             patch.object(mapper, "_extract_page_links", new=AsyncMock(return_value=(page_urls, "Title", 200))):
            await mapper.map_site("https://example.com", sitemap="skip")

        # _fetch_sitemap should be called ZERO times (not even for sub-sitemaps)
        assert sitemap_call_count == 0

    @pytest.mark.asyncio
    async def test_sitemap_default_is_include(self):
        """When no sitemap arg is given, default is 'include' (both strategies)."""
        mapper = Mapper(browser=AsyncMock())
        sitemap_urls = {"https://example.com/s"}
        page_urls = {"https://example.com/p"}

        with patch.object(mapper, "_fetch_sitemap", new=AsyncMock(return_value=sitemap_urls)), \
             patch.object(mapper, "_extract_page_links", new=AsyncMock(return_value=(page_urls, "Title", 200))):
            result = await mapper.map_site("https://example.com")  # no sitemap kwarg

        # Default behavior includes both
        assert "https://example.com/s" in result
        assert "https://example.com/p" in result

    @pytest.mark.asyncio
    async def test_sitemap_invalid_value_falls_back_to_include(self):
        """Invalid sitemap value (e.g. 'bogus') falls back to 'include' behavior."""
        mapper = Mapper(browser=AsyncMock())
        sitemap_urls = {"https://example.com/s"}

        with patch.object(mapper, "_fetch_sitemap", new=AsyncMock(return_value=sitemap_urls)), \
             patch.object(mapper, "_extract_page_links", new=AsyncMock(return_value=(set(), "Title", 200))):
            result = await mapper.map_site("https://example.com", sitemap="bogus")

        # Fallback works — sitemap URL is in result
        assert "https://example.com/s" in result

    @pytest.mark.asyncio
    async def test_sitemap_only_respects_search_filter(self):
        """sitemap='only' still applies the search filter (consistent with other modes)."""
        mapper = Mapper(browser=AsyncMock())
        sitemap_urls = {
            "https://example.com/blog/post-1",
            "https://example.com/blog/post-2",
            "https://example.com/about",
        }

        with patch.object(mapper, "_fetch_sitemap", new=AsyncMock(return_value=sitemap_urls)), \
             patch.object(mapper, "_extract_page_links", new=AsyncMock(return_value=(set(), "Title", 200))):
            result = await mapper.map_site(
                "https://example.com", sitemap="only", search="blog"
            )

        # Only blog URLs returned
        assert "https://example.com/blog/post-1" in result
        assert "https://example.com/blog/post-2" in result
        assert "https://example.com/about" not in result

    @pytest.mark.asyncio
    async def test_sitemap_only_respects_limit(self):
        """sitemap='only' respects the limit parameter."""
        mapper = Mapper(browser=AsyncMock())
        sitemap_urls = {f"https://example.com/p{i}" for i in range(100)}

        with patch.object(mapper, "_fetch_sitemap", new=AsyncMock(return_value=sitemap_urls)), \
             patch.object(mapper, "_extract_page_links", new=AsyncMock(return_value=(set(), "Title", 200))):
            result = await mapper.map_site("https://example.com", sitemap="only", limit=5)

        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_sitemap_include_result_is_sorted(self):
        """sitemap='include' returns URLs sorted (consistent with original behavior)."""
        mapper = Mapper(browser=AsyncMock())
        sitemap_urls = {"https://example.com/z"}
        page_urls = {"https://example.com/a"}

        with patch.object(mapper, "_fetch_sitemap", new=AsyncMock(return_value=sitemap_urls)), \
             patch.object(mapper, "_extract_page_links", new=AsyncMock(return_value=(page_urls, "Title", 200))):
            result = await mapper.map_site("https://example.com", sitemap="include")

        # a comes before z in sorted order
        assert result[0].endswith("/a")
        assert result[1].endswith("/z")
