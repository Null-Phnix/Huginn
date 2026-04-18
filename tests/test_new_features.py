"""Tests for new features: batch scrape, proxy, SSE streaming, rate limiting."""
import os
import pytest
from unittest.mock import MagicMock, patch

from blackcrawl.models import (
    BatchScrapeRequest,
    BatchScrapeResponse,
    BatchScrapeResultItem,
    CrawlRequest,
    ExtractRequest,
    StreamCrawlResponse,
    StreamExtractResponse,
    OutputFormat,
)
from blackcrawl.config import BlackCrawlConfig, ProxyConfig, load_config


# ─── Batch Scrape Models ──────────────────────────────────────────────────────

class TestBatchScrapeModels:
    def test_batch_request_defaults(self):
        req = BatchScrapeRequest(urls=["https://example.com"])
        assert req.urls == ["https://example.com"]
        assert req.formats == [OutputFormat.MARKDOWN]
        assert req.only_main_content is True
        assert req.timeout == 30000

    def test_batch_request_multiple_urls(self):
        urls = [f"https://example.com/page/{i}" for i in range(10)]
        req = BatchScrapeRequest(urls=urls)
        assert len(req.urls) == 10

    def test_batch_request_max_urls(self):
        with pytest.raises(Exception):
            BatchScrapeRequest(urls=[f"https://example.com/{i}" for i in range(51)])

    def test_batch_request_with_formats(self):
        req = BatchScrapeRequest(
            urls=["https://example.com"],
            formats=[OutputFormat.MARKDOWN, OutputFormat.HTML],
        )
        assert len(req.formats) == 2

    def test_batch_result_item_success(self):
        item = BatchScrapeResultItem(url="https://example.com", success=True)
        assert item.url == "https://example.com"
        assert item.success is True
        assert item.data is None
        assert item.error is None

    def test_batch_result_item_error(self):
        item = BatchScrapeResultItem(
            url="https://example.com", success=False, error="Timeout"
        )
        assert item.success is False
        assert item.error == "Timeout"

    def test_batch_response(self):
        response = BatchScrapeResponse(
            success=True,
            data=[
                BatchScrapeResultItem(url="https://example.com", success=True)
            ],
        )
        assert response.success is True
        assert len(response.data) == 1

    def test_batch_response_empty(self):
        response = BatchScrapeResponse(success=True)
        assert response.data == []


# ─── Proxy Config ──────────────────────────────────────────────────────────────

class TestProxyConfig:
    def test_default_proxy_config(self):
        config = BlackCrawlConfig()
        assert config.proxy.server is None or config.proxy.server == ""
        assert config.proxy.username is None or config.proxy.username == ""
        assert config.proxy.password is None or config.proxy.password == ""

    def test_proxy_config_from_env(self):
        with patch.dict(os.environ, {
            "BLACKCRAWL_PROXY_SERVER": "http://proxy.example.com:8080",
            "BLACKCRAWL_PROXY_USERNAME": "user",
            "BLACKCRAWL_PROXY_PASSWORD": "pass",
        }):
            config = load_config()
            assert config.proxy.server == "http://proxy.example.com:8080"
            assert config.proxy.username == "user"
            assert config.proxy.password == "pass"

    def test_rate_limit_from_env(self):
        with patch.dict(os.environ, {"BLACKCRAWL_RATE_LIMIT": "50/minute"}):
            config = load_config()
            assert config.server.rate_limit == "50/minute"


# ─── SSE Streaming Models ──────────────────────────────────────────────────────

class TestSSEModels:
    def test_stream_crawl_response(self):
        resp = StreamCrawlResponse(type="document", data={"url": "https://example.com"})
        assert resp.type == "document"
        assert resp.data["url"] == "https://example.com"

    def test_stream_extract_response(self):
        resp = StreamExtractResponse(type="progress", data={"step": "scraping"})
        assert resp.type == "progress"
        assert resp.data["step"] == "scraping"

    def test_crawl_request_stream_default(self):
        req = CrawlRequest(url="https://example.com")
        assert req.stream is False

    def test_crawl_request_stream_true(self):
        req = CrawlRequest(url="https://example.com", stream=True)
        assert req.stream is True

    def test_extract_request_stream_default(self):
        req = ExtractRequest(urls=["https://example.com"])
        assert req.stream is False

    def test_extract_request_stream_true(self):
        req = ExtractRequest(urls=["https://example.com"], stream=True)
        assert req.stream is True


# ─── API Routes ─────────────────────────────────────────────────────────────────

class TestAPIRoutes:
    def test_batch_scrape_route_exists(self):
        from blackcrawl.api import create_app
        app = create_app()
        routes = [r.path for r in app.routes]
        assert "/v1/batch/scrape" in routes

    def test_stream_scrape_routes_exist(self):
        from blackcrawl.api import create_app
        app = create_app()
        routes = [(r.path, r.methods if hasattr(r, 'methods') else set()) for r in app.routes]
        # Just check /v1/crawl exists (streaming is handled by same endpoint)
        paths = [r[0] for r in routes]
        assert "/v1/crawl" in paths

    def test_rate_limit_middleware(self):
        from blackcrawl.api import create_app
        app = create_app()
        # Check slowapi is registered
        assert hasattr(app.state, 'limiter')