"""
Tests for BlackCrawl API — FastAPI endpoint tests.

Uses httpx AsyncClient with FastAPI's TestClient for in-process testing.
No real browser needed — browser operations are mocked.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from blackcrawl.config import BlackCrawlConfig
from blackcrawl.models import ScrapeData, OutputFormat


class TestHealthEndpoint:
    """Test /health endpoint."""

    @pytest.mark.asyncio
    async def test_health_check(self):
        from blackcrawl.api import create_app
        config = BlackCrawlConfig()
        app = create_app(config)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert "version" in data


class TestScrapeEndpoint:
    """Test /v1/scrape endpoint."""

    @pytest.mark.asyncio
    async def test_scrape_request_model(self):
        """ScrapeRequest should parse correctly with all fields."""
        from blackcrawl.models import ScrapeRequest
        req = ScrapeRequest(
            url="https://example.com",
            formats=["markdown", "html"],
            only_main_content=True,
            timeout=15000,
        )
        assert req.url == "https://example.com"
        assert len(req.formats) == 2
        assert req.timeout == 15000

    @pytest.mark.asyncio
    async def test_scrape_with_alias_fields(self):
        """Should accept camelCase API fields."""
        from blackcrawl.models import ScrapeRequest
        req = ScrapeRequest(
            **{
                "url": "https://example.com",
                "waitFor": 5000,
                "onlyMainContent": False,
            }
        )
        assert req.wait_for == 5000
        assert req.only_main_content is False


class TestCrawlEndpoint:
    """Test /v1/crawl endpoint."""

    @pytest.mark.asyncio
    async def test_crawl_request_model(self):
        from blackcrawl.models import CrawlRequest
        req = CrawlRequest(url="https://example.com", max_depth=5, limit=50)
        assert req.url == "https://example.com"
        assert req.max_depth == 5
        assert req.limit == 50

    @pytest.mark.asyncio
    async def test_crawl_request_with_scrape_options(self):
        from blackcrawl.models import CrawlRequest, ScrapeOptions
        req = CrawlRequest(
            url="https://example.com",
            scrape_options=ScrapeOptions(
                formats=["markdown", "html"],
                only_main_content=False,
            ),
        )
        assert req.scrape_options is not None
        assert len(req.scrape_options.formats) == 2


class TestMapEndpoint:
    """Test /v1/map endpoint."""

    @pytest.mark.asyncio
    async def test_map_request_model(self):
        from blackcrawl.models import MapRequest
        req = MapRequest(url="https://example.com", search="api", limit=100)
        assert req.url == "https://example.com"
        assert req.search == "api"
        assert req.limit == 100

    @pytest.mark.asyncio
    async def test_map_response_model(self):
        from blackcrawl.models import MapResponse
        resp = MapResponse(success=True, links=["https://example.com/a", "https://example.com/b"])
        assert resp.success is True
        assert len(resp.links) == 2


class TestExtractEndpoint:
    """Test /v1/extract endpoint."""

    @pytest.mark.asyncio
    async def test_extract_request_model(self):
        from blackcrawl.models import ExtractRequest
        req = ExtractRequest(
            urls=["https://example.com"],
            prompt="Extract the main content",
            schema_={"type": "object", "properties": {"title": {"type": "string"}}},
            max_retries=5,
        )
        assert len(req.urls) == 1
        assert req.prompt == "Extract the main content"
        assert req.max_retries == 5
        assert req.mental_model is True


class TestSearchEndpoint:
    """Test /v1/search endpoint."""

    @pytest.mark.asyncio
    async def test_search_request_model(self):
        from blackcrawl.models import SearchRequest, SearchOptions
        req = SearchRequest(
            query="test query",
            search_options=SearchOptions(limit=10),
            fallback_chain=True,
        )
        assert req.query == "test query"
        assert req.search_options.limit == 10
        assert req.fallback_chain is True


class TestJobEndpoints:
    """Test job listing and deletion."""

    @pytest.mark.asyncio
    async def test_job_info_model(self):
        from datetime import datetime
        from blackcrawl.models import JobInfo, JobStatus
        info = JobInfo(
            id="test-123",
            type="crawl",
            status=JobStatus.COMPLETED,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            completed=10,
            total=20,
        )
        assert info.type == "crawl"
        assert info.status == JobStatus.COMPLETED


class TestAPICreation:
    """Test FastAPI app creation and configuration."""

    def test_create_app_default(self):
        from blackcrawl.api import create_app
        config = BlackCrawlConfig()
        app = create_app(config)
        assert app is not None
        assert app.title == "BlackCrawl"

    def test_create_app_custom_config(self):
        from blackcrawl.api import create_app
        config = BlackCrawlConfig()
        config.server.port = 9999
        config.server.api_key = "test-key"
        app = create_app(config)
        assert app.state.config.server.port == 9999
        assert app.state.config.server.api_key == "test-key"

    def test_app_has_all_routes(self):
        from blackcrawl.api import create_app
        app = create_app(BlackCrawlConfig())
        routes = [r.path for r in app.routes]
        assert "/health" in routes
        assert "/v1/scrape" in routes
        assert "/v1/crawl" in routes
        assert "/v1/crawl/{job_id}" in routes
        assert "/v1/map" in routes
        assert "/v1/extract" in routes
        assert "/v1/extract/{job_id}" in routes
        assert "/v1/search" in routes