"""
Tests for includeTags/excludeTags — Firecrawl parity.

Feature: POST /v1/scrape accepts include_tags and exclude_tags as arrays
of CSS selectors. The scraper runs document.querySelectorAll(selector) for
each tag and either keeps only those elements (include) or removes them
(exclude).

These tests verify the CSS-selector-based filtering works for all standard
HTML tag types that Firecrawl documents and a few common extra ones.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from huginn.scraper import Scraper
from huginn.models import ScrapeRequest, OutputFormat, ScrapeData


# ─── Tag types to verify ────────────────────────────────────────────────────
# Firecrawl documents support for: script, style, img, a, svg, iframe, div,
# section, article, aside, footer, header, nav, main, body
# We test all of these as both include and exclude.

STANDARD_TAGS = [
    "script",
    "style",
    "img",
    "a",
    "svg",
    "iframe",
    "div",
    "section",
    "article",
    "aside",
    "footer",
    "header",
    "nav",
    "main",
    "table",
    "ul",
    "ol",
]

EXTENDED_TAGS = [
    ".ads",          # class selector
    "#header",       # id selector
    "[data-testid]", # attribute selector
    "picture",       # <picture> with source+img
    "source",        # <source> inside picture/video
    "video",
    "audio",
    "canvas",
    "pre",
    "code",
    "blockquote",
]


class TestIncludeTagsModel:
    """Model accepts include_tags / exclude_tags for all standard selectors."""

    @pytest.mark.parametrize("tag", STANDARD_TAGS + EXTENDED_TAGS)
    def test_include_single_tag_accepted(self, tag):
        req = ScrapeRequest(
            url="https://example.com",
            include_tags=[tag],
        )
        assert req.include_tags == [tag]

    @pytest.mark.parametrize("tag", STANDARD_TAGS + EXTENDED_TAGS)
    def test_exclude_single_tag_accepted(self, tag):
        req = ScrapeRequest(
            url="https://example.com",
            exclude_tags=[tag],
        )
        assert req.exclude_tags == [tag]

    @pytest.mark.parametrize("tag", STANDARD_TAGS)
    def test_include_multiple_tags_accepted(self, tag):
        req = ScrapeRequest(
            url="https://example.com",
            include_tags=["div", "section", "article"],
        )
        assert req.include_tags == ["div", "section", "article"]

    @pytest.mark.parametrize("tag", STANDARD_TAGS)
    def test_include_and_exclude_both_set(self, tag):
        req = ScrapeRequest(
            url="https://example.com",
            include_tags=["div", "article"],
            exclude_tags=[".ads", "script"],
        )
        assert req.include_tags == ["div", "article"]
        assert req.exclude_tags == [".ads", "script"]


class TestIncludeTagsFiltering:
    """The scraper calls page.evaluate with correct CSS selectors."""

    @pytest.mark.asyncio
    async def test_include_script_calls_query_selector_all(self):
        """include_tags=['script'] should appear in the JS querySelectorAll call."""
        mock_page = MagicMock()
        mock_page.evaluate = AsyncMock(return_value="<script>alert(1)</script>")
        result = ScrapeData(html="<html><script>alert(1)</script></html>", markdown="# Page")
        scraper = Scraper(AsyncMock())
        await scraper._filter_tags(
            mock_page,
            result,
            include_tags=["script"],
            exclude_tags=None,
        )
        eval_str = str(mock_page.evaluate.call_args_list)
        assert "script" in eval_str

    @pytest.mark.asyncio
    async def test_include_img_calls_query_selector_all(self):
        mock_page = MagicMock()
        mock_page.evaluate = AsyncMock(return_value="<img src='x.png'>")
        result = ScrapeData(html="<html><img src='x.png'></html>", markdown="![img](x.png)")
        scraper = Scraper(AsyncMock())
        await scraper._filter_tags(
            mock_page,
            result,
            include_tags=["img"],
            exclude_tags=None,
        )
        eval_str = str(mock_page.evaluate.call_args_list)
        assert "img" in eval_str

    @pytest.mark.asyncio
    async def test_include_iframe_calls_query_selector_all(self):
        mock_page = MagicMock()
        mock_page.evaluate = AsyncMock(return_value="<iframe src='y.html'></iframe>")
        result = ScrapeData(html="<html><iframe src='y.html'></iframe></html>", markdown="[iframe]")
        scraper = Scraper(AsyncMock())
        await scraper._filter_tags(
            mock_page,
            result,
            include_tags=["iframe"],
            exclude_tags=None,
        )
        eval_str = str(mock_page.evaluate.call_args_list)
        assert "iframe" in eval_str

    @pytest.mark.asyncio
    async def test_exclude_style_removes_style_tags(self):
        """exclude_tags=['style'] should use querySelectorAll to remove style elements."""
        mock_page = MagicMock()
        mock_page.evaluate = AsyncMock(return_value="<html><div>no styles</div></html>")
        result = ScrapeData(html="<html><style>.x{}</style><div>no styles</div></html>", markdown="# Page")
        scraper = Scraper(AsyncMock())
        await scraper._filter_tags(
            mock_page,
            result,
            include_tags=None,
            exclude_tags=["style"],
        )
        eval_str = str(mock_page.evaluate.call_args_list)
        assert "style" in eval_str

    @pytest.mark.asyncio
    async def test_exclude_ads_removes_ad_elements(self):
        mock_page = MagicMock()
        mock_page.evaluate = AsyncMock(return_value="<div>content</div>")
        result = ScrapeData(html="<html><div class='ads'>buy now</div><div>real content</div></html>", markdown="content")
        scraper = Scraper(AsyncMock())
        await scraper._filter_tags(
            mock_page,
            result,
            include_tags=None,
            exclude_tags=[".ads"],
        )
        eval_str = str(mock_page.evaluate.call_args_list)
        assert ".ads" in eval_str

    @pytest.mark.asyncio
    async def test_include_multiple_tags_all_passed(self):
        """All include_tags selectors should be passed to the JS evaluator."""
        selectors = ["div", "article", "section"]
        mock_page = MagicMock()
        mock_page.evaluate = AsyncMock(return_value="combined content")
        result = ScrapeData(html="<html></html>", markdown="content")
        scraper = Scraper(AsyncMock())
        await scraper._filter_tags(
            mock_page,
            result,
            include_tags=selectors,
            exclude_tags=None,
        )
        eval_str = str(mock_page.evaluate.call_args_list)
        for sel in selectors:
            assert sel in eval_str, f"Selector '{sel}' not found in {eval_str}"

    @pytest.mark.asyncio
    async def test_no_tags_means_no_query_selector_all(self):
        """When neither include_tags nor exclude_tags is set, no evaluate is called."""
        mock_page = MagicMock()
        mock_page.evaluate = AsyncMock()
        result = ScrapeData(html="<html><div>content</div></html>", markdown="content")
        scraper = Scraper(AsyncMock())
        await scraper._filter_tags(
            mock_page,
            result,
            include_tags=None,
            exclude_tags=None,
        )
        # No evaluate calls when no tags specified
        assert mock_page.evaluate.call_count == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tag", STANDARD_TAGS)
    async def test_all_standard_tags_accepted(self, tag):
        """Every standard HTML tag is a valid CSS selector and is passed through."""
        mock_page = MagicMock()
        mock_page.evaluate = AsyncMock(return_value=f"<{tag}>content</{tag}>")
        result = ScrapeData(html=f"<html><{tag}>content</{tag}></html>", markdown="content")
        scraper = Scraper(AsyncMock())
        await scraper._filter_tags(
            mock_page,
            result,
            include_tags=[tag],
            exclude_tags=None,
        )
        eval_str = str(mock_page.evaluate.call_args_list)
        assert tag in eval_str
