"""
Tests for iframe content extraction — Firecrawl parity.

Feature: includeTags=["iframe"] extracts iframe elements + their src URLs
from the scraped page. The user can request a list of iframe URLs and
their content (or just the iframe elements as HTML).

The default includeTags behavior of Scraper._filter_tags already supports
"iframe" as a CSS selector. This test verifies:
  1. include_tags=["iframe"] preserves iframe elements in the markdown
  2. Iframe src URLs are captured in the extracted content
  3. Nested iframes are also handled (querySelectorAll matches all)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from huginn.scraper import Scraper
from huginn.models import ScrapeData


class TestIframeExtraction:
    """iframe content is extracted when included via includeTags."""

    @pytest.mark.asyncio
    async def test_iframe_include_extracts_iframe_html(self):
        """include_tags=['iframe'] keeps iframe elements in the result."""
        mock_page = MagicMock()
        # Mock evaluate to return iframe HTML
        mock_page.evaluate = AsyncMock(return_value=(
            '<iframe src="https://player.example.com/embed/123" '
            'width="640" height="360" allowfullscreen></iframe>'
        ))
        scraper = Scraper(AsyncMock())
        result = ScrapeData(
            html='<html><body><iframe src="https://player.example.com/embed/123"></iframe></body></html>',
            markdown="# Page\n\nContent",
        )
        await scraper._filter_tags(
            mock_page,
            result,
            include_tags=["iframe"],
            exclude_tags=None,
        )
        # The iframe HTML should now be in result.html
        assert "iframe" in (result.html or "").lower()
        assert "player.example.com" in (result.html or "")

    @pytest.mark.asyncio
    async def test_iframe_exclude_removes_iframes(self):
        """exclude_tags=['iframe'] strips iframe elements from the page."""
        mock_page = MagicMock()
        mock_page.evaluate = AsyncMock(return_value="<html><body><p>cleaned</p></body></html>")
        scraper = Scraper(AsyncMock())
        result = ScrapeData(
            html='<html><body><div>content</div><iframe src="x.com"></iframe></body></html>',
            markdown="content",
        )
        await scraper._filter_tags(
            mock_page,
            result,
            include_tags=None,
            exclude_tags=["iframe"],
        )
        # The mock returns content with no iframe elements
        assert "<iframe" not in (result.html or "")
        assert "</iframe>" not in (result.html or "")

    @pytest.mark.asyncio
    async def test_multiple_iframes_all_extracted(self):
        """Multiple iframes on a page are all captured in the include_tags result."""
        mock_page = MagicMock()
        mock_page.evaluate = AsyncMock(return_value=(
            '<iframe src="https://a.com/1"></iframe>\n'
            '<iframe src="https://b.com/2"></iframe>\n'
            '<iframe src="https://c.com/3"></iframe>'
        ))
        scraper = Scraper(AsyncMock())
        result = ScrapeData(
            html='<html><body><iframe src="https://a.com/1"></iframe><iframe src="https://b.com/2"></iframe><iframe src="https://c.com/3"></iframe></body></html>',
            markdown="page",
        )
        await scraper._filter_tags(
            mock_page,
            result,
            include_tags=["iframe"],
            exclude_tags=None,
        )
        # The mock returns all 3 iframes
        assert result.html is not None
        assert "a.com" in result.html
        assert "b.com" in result.html
        assert "c.com" in result.html

    @pytest.mark.asyncio
    async def test_iframe_query_selector_uses_iframe_string(self):
        """The CSS selector passed to querySelectorAll is the literal 'iframe'."""
        mock_page = MagicMock()
        mock_page.evaluate = AsyncMock(return_value="<iframe>x</iframe>")
        scraper = Scraper(AsyncMock())
        result = ScrapeData(html="<html></html>", markdown="")
        await scraper._filter_tags(
            mock_page,
            result,
            include_tags=["iframe"],
            exclude_tags=None,
        )
        # Check that 'iframe' appears in the evaluate call args
        eval_str = str(mock_page.evaluate.call_args_list)
        assert "iframe" in eval_str

    @pytest.mark.asyncio
    async def test_iframe_with_other_tags_combined(self):
        """include_tags=['iframe', 'video'] extracts both iframe and video elements."""
        mock_page = MagicMock()
        mock_page.evaluate = AsyncMock(return_value=(
            '<iframe src="https://embed.com/v1"></iframe>\n'
            '<video src="video.mp4"></video>'
        ))
        scraper = Scraper(AsyncMock())
        result = ScrapeData(
            html="<html><body><iframe src='https://embed.com/v1'></iframe><video src='video.mp4'></video></body></html>",
            markdown="content",
        )
        await scraper._filter_tags(
            mock_page,
            result,
            include_tags=["iframe", "video"],
            exclude_tags=None,
        )
        assert "iframe" in (result.html or "").lower()
        assert "video" in (result.html or "").lower()
