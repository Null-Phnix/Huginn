"""
Tests for Huginn Models — Pydantic schemas validation.
"""

import pytest
from pydantic import ValidationError

from huginn.models import (
    Action,
    ActionType,
    CrawlRequest,
    CrawlStartResponse,
    CrawlStatusResponse,
    ExtractRequest,
    ExtractOptions,
    ExtractStartResponse,
    ExtractStatusResponse,
    JobInfo,
    JobStatus,
    Location,
    MapRequest,
    MapResponse,
    OutputFormat,
    ProxyMode,
    ScrapeData,
    ScrapeRequest,
    ScrapeResponse,
    SearchOptions,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
)


class TestScrapeModels:
    """Test scrape request/response models."""

    def test_scrape_request_defaults(self):
        """Scrape request should have sensible defaults."""
        req = ScrapeRequest(url="https://example.com")
        assert req.url == "https://example.com"
        assert req.formats == [OutputFormat.MARKDOWN]
        assert req.only_main_content is True
        assert req.timeout == 30000
        assert req.stealth_mode is True

    def test_scrape_request_with_actions(self):
        """Should accept browser actions."""
        req = ScrapeRequest(
            url="https://example.com",
            actions=[
                Action(type=ActionType.CLICK, selector="#accept-cookies"),
                Action(type=ActionType.WAIT, amount=2000),
                Action(type=ActionType.SCROLL, direction="down", amount=500),
            ],
        )
        assert len(req.actions) == 3
        assert req.actions[0].type == ActionType.CLICK

    def test_scrape_request_all_formats(self):
        """Should accept all format types."""
        req = ScrapeRequest(
            url="https://example.com",
            formats=[
                OutputFormat.MARKDOWN,
                OutputFormat.HTML,
                OutputFormat.RAW_HTML,
                OutputFormat.SCREENSHOT,
                OutputFormat.LINKS,
            ],
        )
        assert len(req.formats) == 5

    def test_scrape_request_with_extract(self):
        """Should accept extraction options."""
        req = ScrapeRequest(
            url="https://example.com",
            extract=ExtractOptions(
                prompt="Extract the main article text",
                schema_={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                    },
                },
            ),
        )
        assert req.extract is not None
        assert req.extract.prompt == "Extract the main article text"

    def test_scrape_data(self):
        """ScrapeData should hold all content types."""
        data = ScrapeData(
            markdown="# Hello\nWorld",
            html="<h1>Hello</h1><p>World</p>",
            metadata={"url": "https://example.com", "title": "Test"},
        )
        assert data.markdown == "# Hello\nWorld"
        assert data.html is not None

    def test_scrape_response_success(self):
        """Should model a successful response."""
        resp = ScrapeResponse(
            success=True,
            data=ScrapeData(markdown="# Hello", metadata={"url": "https://example.com"}),
        )
        assert resp.success is True
        assert resp.data.markdown == "# Hello"

    def test_scrape_response_error(self):
        """Should model an error response."""
        resp = ScrapeResponse(success=False, error="Navigation failed")
        assert resp.success is False
        assert resp.error == "Navigation failed"


class TestCrawlModels:
    """Test crawl request/response models."""

    def test_crawl_request_defaults(self):
        """Crawl request should have defaults."""
        req = CrawlRequest(url="https://example.com")
        assert req.url == "https://example.com"
        assert req.max_depth is None  # Uses server config default
        assert req.limit is None  # Uses server config default
        assert req.allow_backward_crawling is False
        assert req.allow_external_links is False

    def test_crawl_request_with_paths(self):
        """Should accept include/exclude path filters."""
        req = CrawlRequest(
            url="https://example.com",
            include_paths=["/docs/", "/api/"],
            exclude_paths=["/admin/", "/login"],
            max_depth=5,
        )
        assert len(req.include_paths) == 2
        assert len(req.exclude_paths) == 2
        assert req.max_depth == 5

    def test_crawl_start_response(self):
        """Should model crawl start response."""
        resp = CrawlStartResponse(success=True, id="abc-123", url="/v1/sweep/abc-123")
        assert resp.success is True
        assert resp.id == "abc-123"

    def test_crawl_status_response(self):
        """Should model crawl status response."""
        resp = CrawlStatusResponse(
            success=True,
            status=JobStatus.RUNNING,
            completed=5,
            total=20,
        )
        assert resp.status == JobStatus.RUNNING
        assert resp.completed == 5


class TestMapModels:
    """Test map request/response models."""

    def test_map_request_defaults(self):
        req = MapRequest(url="https://example.com")
        assert req.url == "https://example.com"
        assert req.search is None
        assert req.limit == 5000

    def test_map_response(self):
        resp = MapResponse(success=True, links=["https://example.com/about", "https://example.com/contact"])
        assert resp.success is True
        assert len(resp.links) == 2


class TestExtractModels:
    """Test extract request/response models."""

    def test_extract_request_defaults(self):
        req = ExtractRequest(urls=["https://example.com"])
        assert req.urls == ["https://example.com"]
        assert req.mental_model is True
        assert req.max_retries == 3

    def test_extract_status_response(self):
        resp = ExtractStatusResponse(
            success=True,
            status=JobStatus.COMPLETED,
            data={"title": "Example", "content": "Hello"},
        )
        assert resp.success is True
        assert resp.data["title"] == "Example"


class TestSearchModels:
    """Test search request/response models."""

    def test_search_request_defaults(self):
        req = SearchRequest(query="test query")
        assert req.query == "test query"
        assert req.fallback_chain is True

    def test_search_response(self):
        resp = SearchResponse(
            success=True,
            data=[
                SearchResultItem(
                    markdown="# Result 1",
                    metadata={"title": "Test", "url": "https://example.com"},
                ),
            ],
        )
        assert len(resp.data) == 1


class TestModelFields:
    """Test that model fields accept snake_case input."""

    def test_scrape_request_aliases(self):
        req = ScrapeRequest(
            url="https://example.com",
            **{
                "wait_for": 5000,
                "only_main_content": False,
                "include_tags": ["article"],
                "exclude_tags": [".ads"],
                "stealth_mode": False,
            }
        )
        assert req.wait_for == 5000
        assert req.only_main_content is False
        assert req.include_tags == ["article"]
        assert req.stealth_mode is False

    def test_crawl_request_aliases(self):
        req = CrawlRequest(
            url="https://example.com",
            **{
                "max_depth": 5,
                "allow_backward_crawling": True,
                "allow_external_links": False,
                "include_paths": ["/docs/"],
                "exclude_paths": ["/admin/"],
            }
        )
        assert req.max_depth == 5
        assert req.allow_backward_crawling is True