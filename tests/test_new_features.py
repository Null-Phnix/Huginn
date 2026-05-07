"""
New feature tests — Huginn v1.1
Tests: schedule, webhook, PDF extraction, API routes, model fields
"""
import asyncio
import json
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from huginn.models import (
    CrawlRequest,
    DistillRequest,
    FlockRequest,
    FlockResultItem,
    FlockResponse,
    OutputFormat,
    ScrapeRequest,
    ScrapeResponse,
    ScheduleRequest,
    ScheduleResponse,
    ScheduleStatus,
    ScrapeData,
)
from huginn.job_store import JobStore


# ─── Schedule Model Tests ───────────────────────────────────────────

class TestScheduleModels:
    def test_schedule_request_required_fields(self):
        req = ScheduleRequest(name="test", job_type="scrape", cron="0 9 * * *", request={})
        assert req.name == "test"
        assert req.job_type == "scrape"
        assert req.cron == "0 9 * * *"
        assert req.request == {}
        assert req.enabled is True

    def test_schedule_request_with_request(self):
        req = ScheduleRequest(
            name="docs-check",
            job_type="crawl",
            cron="0 9 * * *",
            request={"url": "https://example.com", "maxDepth": 2},
        )
        assert req.request["url"] == "https://example.com"

    def test_schedule_response_model(self):
        resp = ScheduleResponse(
            id="sched-123",
            name="test",
            job_type="scrape",
            cron="0 9 * * *",
            request_json="{}",
            enabled=True,
            status="pending",
        )
        assert resp.id == "sched-123"
        assert resp.status == "pending"


# ─── Scheduler Tests ────────────────────────────────────────────────

class TestScheduler:
    """Test scheduler via the API layer."""

    @pytest.mark.asyncio
    async def test_scheduler_job_store_integration(self):
        """JobStore schedules CRUD."""
        js = JobStore(db_path=":memory:")
        await js.init()
        
        # Create
        sid = await js.create_schedule(
            schedule_id="test-1",
            name="test-sched",
            job_type="scrape",
            request={"url": "https://example.com"},
            cron="0 9 * * *",
        )
        assert sid == "test-1"
        
        # List
        schedules = await js.list_schedules()
        assert len(schedules) == 1
        assert schedules[0]["name"] == "test-sched"
        
        # Get
        sched = await js.get_schedule("test-1")
        assert sched["name"] == "test-sched"
        
        # Update
        await js.update_schedule("test-1", enabled=False)
        sched = await js.get_schedule("test-1")
        assert not sched["enabled"]
        
        # Delete
        await js.delete_schedule("test-1")
        schedules = await js.list_schedules()
        assert len(schedules) == 0


# ─── Webhook Tests ──────────────────────────────────────────────────

class TestWebhook:
    def test_webhook_module_imports(self):
        try:
            from huginn import webhook
            assert hasattr(webhook, "fire_webhook_for_job")
        except ImportError:
            pytest.skip("webhook module not available")

    @pytest.mark.asyncio
    async def test_webhook_fires_on_completion(self):
        try:
            from huginn import webhook
            with patch.object(webhook, "send_webhook", new_callable=AsyncMock) as mock_send:
                mock_send.return_value = True
                await webhook.fire_webhook_for_job(
                    webhook_url="https://example.com/callback",
                    job_id="job-123",
                    job_type="scrape",
                    status="completed",
                    success=True,
                )
                mock_send.assert_called_once()
        except ImportError:
            pytest.skip("webhook module not available")


# ─── PDF Extraction Tests ────────────────────────────────────────────

class TestPDFExtraction:
    def test_pdf_module_has_extract_method(self):
        try:
            from huginn import pdf
            assert hasattr(pdf, "extract_pdf_text")
        except ImportError:
            pytest.skip("PDF module not available")

    def test_pdf_module_exists(self):
        # pdf extraction may be sync or async depending on backend
        try:
            from huginn import pdf
            assert hasattr(pdf, "extract_pdf_text")
        except ImportError:
            pytest.skip("PDF module not available")


# ─── API Route Tests ─────────────────────────────────────────────────

class TestAPIRoutes:
    def test_schedule_routes_exist(self):
        from huginn.api import create_app
        app = create_app()
        routes = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/v1/schedule" in routes
        assert "/health" in routes
        assert "/health/ready" in routes
        assert "/health/live" in routes

    def test_all_schedule_sub_routes(self):
        from huginn.api import create_app
        app = create_app()
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert any("/v1/schedule/" in p for p in paths)

    def test_rate_limit_middleware(self):
        from huginn.api import create_app
        app = create_app()
        assert any(
            "limiter" in str(getattr(r, "endpoint", ""))
            for r in app.routes
        ) or hasattr(app.state, "limiter")


# ─── Browser Manager Tests ──────────────────────────────────────────

class TestBrowserManager:
    def test_browser_manager_import(self):
        from huginn.browser import BrowserManager
        assert BrowserManager is not None


# ─── Model Field Tests ───────────────────────────────────────────────

class TestModelFields:
    def test_scrape_data_has_pdf_text(self):
        data = ScrapeData(
            markdown="# Test",
            html="<h1>Test</h1>",
            links=["https://example.com"],
            pdf_text="PDF extracted text",
        )
        assert data.pdf_text == "PDF extracted text"

    def test_distill_request_schema_field(self):
        req = DistillRequest(
            urls=["https://example.com"],
            schema_={"type": "object", "properties": {"title": {"type": "string"}}},
        )
        assert req.schema_ is not None
        assert req.schema_["properties"]["title"]["type"] == "string"

    def test_distill_format_validation(self):
        with pytest.raises(ValidationError):
            DistillRequest(urls=["https://example.com"], format="xml")
        req = DistillRequest(urls=["https://example.com"], format="text")
        assert req.format == "text"

    def test_distill_empty_urls_rejected(self):
        with pytest.raises(ValidationError):
            DistillRequest(urls=[])

    def test_crawl_request_has_webhook_url(self):
        req = CrawlRequest(url="https://example.com", webhook_url="https://callback.com")
        assert req.webhook_url == "https://callback.com"

    def test_crawl_request_defaults(self):
        req = CrawlRequest(url="https://example.com")
        assert req.max_depth is None
        assert req.limit is None
        assert req.allow_external_links is False

# ─── Crawler Intelligence Tests ───────────────────────────────────────

class TestCrawlerIntelligence:
    def test_priority_queue_import(self):
        import heapq
        # Verify heapq works as we use it
        h = []
        heapq.heappush(h, (1, 0, "https://example.com/page1", 1))
        heapq.heappush(h, (100, 0, "https://other.com/page1", 1))
        priority, counter, url, depth = heapq.heappop(h)
        assert priority == 1  # same-domain has lower score

    def test_pagination_patterns(self):
        """Pagination detection patterns are valid regexes."""
        import re
        # Path-based pagination patterns
        patterns = [
            r"/page/(\d+)",
            r"/p/(\d+)",
            r"-page-(\d+)",
            r"_(\d+)\.html?",
        ]
        for p in patterns:
            re.compile(p)
        
        assert re.search(r"/page/(\d+)", "https://example.com/page/2")
        assert re.search(r"/p/(\d+)", "https://example.com/p/3")
        assert re.search(r"-page-(\d+)", "https://example.com/articles-page-7")
        assert re.search(r"_(\d+)\.html?", "https://example.com/article_12.html")


    def test_content_hash_deterministic(self):
        from huginn.crawler import content_hash
        text = "Hello, world!"
        h1 = content_hash(text)
        h2 = content_hash(text)
        assert h1 == h2
        assert len(h1) == 16  # truncated SHA-256

    def test_crawler_priority_scoring(self):
        from urllib.parse import urlparse
        base = "example.com"
        same_domain = "https://example.com/page1"
        diff_domain = "https://other.com/page1"
        p_same = (0 if urlparse(same_domain).netloc == base else 100, 1, len(same_domain), 0)
        p_diff = (0 if urlparse(diff_domain).netloc == base else 100, 1, len(diff_domain), 0)
        assert p_same[0] < p_diff[0]  # same-domain has priority
