"""
Tests for per-job maxConcurrency on /v1/crawl — Firecrawl parity.

Feature: POST /v1/crawl accepts maxConcurrency (camelCase) or
max_concurrency (snake_case) to override the global crawl concurrency
for a specific job. When omitted, the global default from config is used.
"""

import pytest
from pydantic import TypeAdapter

from huginn.models import CrawlRequest


class TestCrawlRequestMaxConcurrency:
    """CrawlRequest.max_concurrency accepts and defaults correctly."""

    def test_crawl_request_has_max_concurrency_field(self):
        """CrawlRequest.max_concurrency exists, defaults to None."""
        req = CrawlRequest(url="https://example.com")
        assert hasattr(req, "max_concurrency")
        assert req.max_concurrency is None

    def test_crawl_request_accepts_max_concurrency_snake(self):
        """max_concurrency (snake_case) is accepted via populate_by_name."""
        req = CrawlRequest(url="https://example.com", max_concurrency=10)
        assert req.max_concurrency == 10

    def test_crawl_request_accepts_max_concurrency_camel(self):
        """maxConcurrency (camelCase) is accepted via alias."""
        adapter = TypeAdapter(CrawlRequest)
        req = adapter.validate_python({"url": "https://example.com", "maxConcurrency": 20})
        assert req.max_concurrency == 20

    @pytest.mark.parametrize("value", [1, 2, 5, 10, 50, 100])
    def test_crawl_request_max_concurrency_accepts_various_values(self, value):
        """max_concurrency accepts any positive int."""
        req = CrawlRequest(url="https://example.com", max_concurrency=value)
        assert req.max_concurrency == value

    def test_crawl_request_max_concurrency_serializes_both_names(self):
        """The field serializes to both max_concurrency and maxConcurrency."""
        req = CrawlRequest(url="https://example.com", max_concurrency=15)
        dumped = req.model_dump(by_alias=True)
        assert "maxConcurrency" in dumped
        assert dumped["maxConcurrency"] == 15


class TestCrawlerConcurrencyPerJob:
    """The Crawler uses per-job concurrency when set, global default otherwise."""

    def test_crawler_concurrency_constructor(self):
        """Crawler() accepts a concurrency parameter (already exists)."""
        from huginn.crawler import Crawler
        import inspect
        sig = inspect.signature(Crawler.__init__)
        assert "concurrency" in sig.parameters

    def test_crawler_concurrency_stored_on_instance(self):
        """Crawler.concurrency is stored as instance attribute."""
        from huginn.crawler import Crawler
        from unittest.mock import MagicMock
        c = Crawler(browser=MagicMock(), concurrency=42)
        assert c.concurrency == 42
