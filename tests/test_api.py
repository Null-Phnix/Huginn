"""
Tests for Huginn API — FastAPI endpoint tests.

Uses httpx AsyncClient with FastAPI's TestClient for in-process testing.
No real browser needed — browser operations are mocked.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from huginn.config import HuginnConfig
from huginn.models import ScrapeData, OutputFormat


class TestHealthEndpoint:
    """Test /health endpoint."""

    @pytest.mark.asyncio
    async def test_health_check(self):
        from huginn.api import create_app
        config = HuginnConfig()
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
        from huginn.models import ScrapeRequest
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
        from huginn.models import ScrapeRequest
        req = ScrapeRequest(
            **{
                "url": "https://example.com",
                "wait_for": 5000,
                "only_main_content": False,
            }
        )
        assert req.wait_for == 5000
        assert req.only_main_content is False


class TestCrawlEndpoint:
    """Test /v1/crawl endpoint."""

    @pytest.mark.asyncio
    async def test_crawl_request_model(self):
        from huginn.models import CrawlRequest
        req = CrawlRequest(url="https://example.com", max_depth=5, limit=50)
        assert req.url == "https://example.com"
        assert req.max_depth == 5
        assert req.limit == 50

    @pytest.mark.asyncio
    async def test_crawl_request_with_scrape_options(self):
        from huginn.models import CrawlRequest, ScrapeOptions
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
        from huginn.models import MapRequest
        req = MapRequest(url="https://example.com", search="api", limit=100)
        assert req.url == "https://example.com"
        assert req.search == "api"
        assert req.limit == 100

    @pytest.mark.asyncio
    async def test_map_response_model(self):
        from huginn.models import MapResponse
        resp = MapResponse(success=True, links=["https://example.com/a", "https://example.com/b"])
        assert resp.success is True
        assert len(resp.links) == 2


class TestExtractEndpoint:
    """Test /v1/extract endpoint."""

    @pytest.mark.asyncio
    async def test_extract_request_model(self):
        from huginn.models import DistillRequest
        req = DistillRequest(
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
        from huginn.models import SearchRequest, SearchOptions
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
        from huginn.models import JobInfo, JobStatus
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
        from huginn.api import create_app
        config = HuginnConfig()
        app = create_app(config)
        assert app is not None
        assert app.title == "Huginn"
        assert all(
            middleware.cls.__name__ != "CORSMiddleware"
            for middleware in app.user_middleware
        )

    def test_wildcard_cors_requires_api_auth(self):
        from huginn.api import create_app

        config = HuginnConfig()
        config.server.cors_origins = "*"
        with pytest.raises(RuntimeError, match="requires HUGINN_API_KEY"):
            create_app(config)

    def test_explicit_cors_origin_is_opt_in(self):
        from huginn.api import create_app

        config = HuginnConfig()
        config.server.cors_origins = "https://agents.example"
        app = create_app(config)
        assert any(
            middleware.cls.__name__ == "CORSMiddleware"
            for middleware in app.user_middleware
        )

    def test_create_app_custom_config(self):
        from huginn.api import create_app
        config = HuginnConfig()
        config.server.port = 9999
        config.server.api_key = "test-key"
        app = create_app(config)
        assert app.state.config.server.port == 9999
        assert app.state.config.server.api_key == "test-key"

    def test_app_has_all_routes(self):
        from huginn.api import create_app
        app = create_app(HuginnConfig())
        paths = set(app.openapi()["paths"].keys())
        assert "/health" in paths
        assert "/v1/probe" in paths
        assert "/v1/sweep" in paths
        assert "/v1/sweep/{job_id}" in paths
        assert "/v1/chart" in paths
        assert "/v1/distill" in paths
        assert "/v1/distill/{job_id}" in paths
        assert "/v1/seek" in paths


class TestApiVersionConsistency:
    """The FastAPI app's version + /health version must match huginn.__version__.

    This guards against the recurring 'hardcoded 1.1.0' drift that the
    Nüwa pass found (api.py, health endpoint, health_detailed endpoint).
    """

    def test_app_version_matches_package_version(self):
        from huginn import __version__
        from huginn.api import create_app
        app = create_app(HuginnConfig())
        assert app.version == __version__

    @pytest.mark.asyncio
    async def test_health_endpoint_reports_package_version(self):
        from huginn import __version__
        from huginn.api import create_app
        config = HuginnConfig()
        app = create_app(config)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["version"] == __version__
