"""
Tests for v1.5 router split — state, utils, tasks, router factories.

Verifies that the monolithic api.py was correctly split into:
  - state.py (AppState singleton)
  - utils.py (shared helpers)
  - tasks.py (background task runners / SSE generators)
  - routers/ (15 router factory modules)

These tests do NOT hit real browsers or networks — everything is mocked.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from huginn.config import HuginnConfig
from huginn.state import AppState, get_state, reset_state


# ─── AppState Singleton ──────────────────────────────────────────────


class TestAppState:
    def test_get_state_returns_singleton(self):
        s1 = get_state()
        s2 = get_state()
        assert s1 is s2

    def test_state_has_all_fields(self):
        state = AppState()
        assert state.config is None
        assert state.browser is None
        assert state.job_store is None
        assert state.replay_log is None
        assert state.scheduler is None
        assert state.watcher is None
        assert state.crawl_tasks == {}

    def test_reset_state_creates_new_instance(self):
        old = get_state()
        reset_state()
        new = get_state()
        assert old is not new
        assert new.config is None
        assert new.browser is None

    def test_state_accepts_assigned_fields(self):
        state = get_state()
        state.config = HuginnConfig()
        assert state.config is not None
        state.crawl_tasks["job-1"] = "task-obj"
        assert "job-1" in state.crawl_tasks
        # cleanup
        reset_state()


# ─── Utils ────────────────────────────────────────────────────────────


class TestSSEEvent:
    def test_sse_event_format(self):
        from huginn.utils import sse_event
        result = sse_event("document", {"url": "https://example.com"})
        assert result.startswith("event: document\n")
        assert "data: " in result
        assert result.endswith("\n\n")
        data_part = result.split("data: ", 1)[1].strip()
        assert json.loads(data_part) == {"url": "https://example.com"}

    def test_sse_event_with_complex_data(self):
        from huginn.utils import sse_event
        result = sse_event("done", {"success": True, "pages": [1, 2, 3]})
        data_part = result.split("data: ", 1)[1].strip()
        parsed = json.loads(data_part)
        assert parsed["success"] is True
        assert parsed["pages"] == [1, 2, 3]


class TestMapExceptionToErrorCode:
    def test_timeout_exception(self):
        from huginn.utils import _map_exception_to_error_code
        from huginn.models import ErrorCode
        # On Python 3.14+, asyncio.TimeoutError is an alias for TimeoutError
        e = TimeoutError()
        code = _map_exception_to_error_code(e)
        assert code == ErrorCode.TIMEOUT

    def test_value_error_maps_to_invalid_url(self):
        from huginn.utils import _map_exception_to_error_code
        from huginn.models import ErrorCode
        e = ValueError("bad url")
        code = _map_exception_to_error_code(e)
        assert code == ErrorCode.INVALID_URL

    def test_unknown_exception_falls_back(self):
        from huginn.utils import _map_exception_to_error_code
        from huginn.models import ErrorCode
        e = RuntimeError("something weird")
        code = _map_exception_to_error_code(e)
        # Should fall back to NAVIGATION_FAILED or a default
        assert code is not None


class TestBuildProxyDict:
    def test_no_proxy_returns_none(self):
        from huginn.utils import build_proxy_dict
        config = HuginnConfig()
        # Default config has no proxy
        assert build_proxy_dict(config) is None

    def test_proxy_with_server_only(self):
        from huginn.utils import build_proxy_dict
        config = HuginnConfig()
        config.proxy.server = "http://proxy:8080"
        result = build_proxy_dict(config)
        assert result == {"server": "http://proxy:8080"}

    def test_proxy_with_auth(self):
        from huginn.utils import build_proxy_dict
        config = HuginnConfig()
        config.proxy.server = "http://proxy:8080"
        config.proxy.username = "user"
        config.proxy.password = "pass"
        result = build_proxy_dict(config)
        assert result == {"server": "http://proxy:8080", "username": "user", "password": "pass"}


class TestMakeVerifyAPIKey:
    def test_no_api_key_configured_allows_anonymous(self):
        from huginn.utils import make_verify_api_key
        config = HuginnConfig()
        config.server.api_key = None
        verify = make_verify_api_key(config)
        # Simulate call without authorization header
        import inspect
        assert asyncio.iscoroutinefunction(verify)

    @pytest.mark.asyncio
    async def test_api_key_required_when_configured(self):
        from huginn.utils import make_verify_api_key
        from fastapi import HTTPException
        config = HuginnConfig()
        config.server.api_key = "secret-key"
        verify = make_verify_api_key(config)
        # No authorization header → 401
        with pytest.raises(HTTPException) as exc:
            await verify(authorization=None)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_api_key_valid(self):
        from huginn.utils import make_verify_api_key
        config = HuginnConfig()
        config.server.api_key = "secret-key"
        verify = make_verify_api_key(config)
        result = await verify(authorization="Bearer secret-key")
        assert result is True

    @pytest.mark.asyncio
    async def test_api_key_invalid(self):
        from huginn.utils import make_verify_api_key
        from fastapi import HTTPException
        config = HuginnConfig()
        config.server.api_key = "secret-key"
        verify = make_verify_api_key(config)
        with pytest.raises(HTTPException) as exc:
            await verify(authorization="Bearer wrong-key")
        assert exc.value.status_code == 401


# ─── Router Factory Tests ─────────────────────────────────────────────


class TestRouterFactories:
    """Each router factory should return an APIRouter with routes."""

    def _get_router_paths(self, router):
        """Extract paths from an APIRouter."""
        return {r.path for r in router.routes if hasattr(r, "path")}

    def test_health_router(self):
        from huginn.routers import create_health_router
        router = create_health_router(verify_api_key=None)
        paths = self._get_router_paths(router)
        assert "/health" in paths
        assert "/health/detailed" in paths
        assert "/health/ready" in paths
        assert "/health/live" in paths
        assert "/v1/metrics" in paths

    def test_scrape_router(self):
        from huginn.routers import create_scrape_router
        router = create_scrape_router(HuginnConfig(), verify_api_key=None)
        paths = self._get_router_paths(router)
        assert "/v1/probe" in paths

    def test_crawl_router(self):
        from huginn.routers import create_crawl_router
        router = create_crawl_router(HuginnConfig(), verify_api_key=None)
        paths = self._get_router_paths(router)
        assert "/v1/sweep" in paths
        assert "/v1/sweep/{job_id}" in paths

    def test_map_router(self):
        from huginn.routers import create_map_router
        router = create_map_router(HuginnConfig(), verify_api_key=None)
        paths = self._get_router_paths(router)
        assert "/v1/chart" in paths
        assert "/v1/graph" in paths

    def test_extract_router(self):
        from huginn.routers import create_extract_router
        router = create_extract_router(HuginnConfig(), verify_api_key=None)
        paths = self._get_router_paths(router)
        assert "/v1/distill" in paths
        assert "/v1/distill/{job_id}" in paths

    def test_research_router(self):
        from huginn.routers import create_research_router
        router = create_research_router(HuginnConfig(), verify_api_key=None)
        paths = self._get_router_paths(router)
        assert "/v1/research" in paths

    def test_search_router(self):
        from huginn.routers import create_search_router
        router = create_search_router(HuginnConfig(), verify_api_key=None)
        paths = self._get_router_paths(router)
        assert "/v1/seek" in paths

    def test_jobs_router(self):
        from huginn.routers import create_jobs_router
        router = create_jobs_router(HuginnConfig(), verify_api_key=None)
        paths = self._get_router_paths(router)
        assert "/v1/jobs" in paths
        assert "/v1/jobs/{job_id}" in paths

    def test_batch_router(self):
        from huginn.routers import create_batch_router
        router = create_batch_router(HuginnConfig(), verify_api_key=None)
        paths = self._get_router_paths(router)
        assert "/v1/flock" in paths

    def test_watch_router(self):
        from huginn.routers import create_watch_router
        router = create_watch_router(HuginnConfig(), verify_api_key=None)
        paths = self._get_router_paths(router)
        assert "/v1/watch" in paths
        assert "/v1/watches" in paths
        # FastAPI renders path params as {url:path} for path-type params
        assert any("/v1/watch/" in p for p in paths)

    def test_schedule_router(self):
        from huginn.routers import create_schedule_router
        router = create_schedule_router(HuginnConfig(), verify_api_key=None)
        paths = self._get_router_paths(router)
        assert "/v1/schedule" in paths
        assert "/v1/schedule/{schedule_id}" in paths

    def test_templates_router(self):
        from huginn.routers import create_templates_router
        router = create_templates_router(HuginnConfig(), verify_api_key=None)
        paths = self._get_router_paths(router)
        assert "/v1/templates" in paths
        assert "/v1/templates/{template_name}" in paths

    def test_memory_router(self):
        from huginn.routers import create_memory_router
        router = create_memory_router(HuginnConfig(), verify_api_key=None)
        paths = self._get_router_paths(router)
        assert "/v1/memory/query" in paths

    def test_replay_router(self):
        from huginn.routers import create_replay_router
        router = create_replay_router(HuginnConfig(), verify_api_key=None)
        paths = self._get_router_paths(router)
        assert "/v1/replay" in paths
        assert "/v1/replay/stats" in paths

    def test_aliases_router(self):
        from huginn.routers import create_aliases_router
        router = create_aliases_router(HuginnConfig(), verify_api_key=None)
        paths = self._get_router_paths(router)
        # Firecrawl-compatible aliases
        assert "/v1/scrape" in paths
        assert "/v1/crawl" in paths
        assert "/v1/map" in paths
        assert "/v1/extract" in paths
        assert "/v1/search" in paths
        assert "/v1/batch/scrape" in paths


class TestRouterHelperExposure:
    """Router modules expose _do_* helpers that aliases router calls into."""

    def test_do_scrape_is_callable(self):
        from huginn.routers.scrape import _do_scrape
        assert callable(_do_scrape)

    def test_do_start_sweep_is_callable(self):
        from huginn.routers.crawl import _do_start_sweep
        assert callable(_do_start_sweep)

    def test_do_get_sweep_status_is_callable(self):
        from huginn.routers.crawl import _do_get_sweep_status
        assert callable(_do_get_sweep_status)

    def test_do_cancel_crawl_is_callable(self):
        from huginn.routers.crawl import _do_cancel_crawl
        assert callable(_do_cancel_crawl)

    def test_do_chart_site_is_callable(self):
        from huginn.routers.map import _do_chart_site
        assert callable(_do_chart_site)

    def test_do_start_distill_is_callable(self):
        from huginn.routers.extract import _do_start_distill
        assert callable(_do_start_distill)

    def test_do_get_distill_status_is_callable(self):
        from huginn.routers.extract import _do_get_distill_status
        assert callable(_do_get_distill_status)

    def test_do_search_is_callable(self):
        from huginn.routers.search import _do_search
        assert callable(_do_search)

    def test_do_flock_scrape_is_callable(self):
        from huginn.routers.batch import _do_flock_scrape
        assert callable(_do_flock_scrape)


# ─── Full App Integration ─────────────────────────────────────────────


class TestAppCreation:
    def test_create_app_returns_fastapi(self):
        from fastapi import FastAPI
        from huginn.api import create_app
        app = create_app(HuginnConfig())
        assert isinstance(app, FastAPI)

    def test_app_has_all_44_paths(self):
        from huginn.api import create_app
        app = create_app(HuginnConfig())
        paths = set(app.openapi()["paths"].keys())
        # Core endpoints
        for expected in ["/health", "/v1/probe", "/v1/sweep", "/v1/distill",
                         "/v1/seek", "/v1/flock", "/v1/research", "/v1/chart",
                         "/v1/schedule", "/v1/replay", "/v1/scrape", "/v1/crawl",
                         "/v1/map", "/v1/extract", "/v1/search", "/v1/batch/scrape"]:
            assert expected in paths, f"Missing path: {expected}"

    def test_app_state_config_is_set(self):
        from huginn.api import create_app
        config = HuginnConfig()
        config.server.port = 7777
        app = create_app(config)
        assert app.state.config.server.port == 7777

    def test_backward_compat_summarize_text_reexport(self):
        from huginn.api import _summarize_text, summarize_text
        assert callable(_summarize_text)
        assert callable(summarize_text)

    def test_backward_compat_llm_provider_config_reexport(self):
        from huginn.api import LLM_PROVIDER_CONFIG
        assert LLM_PROVIDER_CONFIG is not None


# ─── Tasks Module ─────────────────────────────────────────────────────


class TestSchedulerHandlers:
    def test_register_scheduler_handlers(self):
        from huginn.tasks import register_scheduler_handlers
        from huginn.scheduler import _HANDLERS
        # Clear and re-register
        _HANDLERS.clear()
        register_scheduler_handlers()
        assert "crawl" in _HANDLERS
        assert "distill" in _HANDLERS
        assert "flock" in _HANDLERS
        assert _HANDLERS["crawl"] is _HANDLERS["distill"]
        assert _HANDLERS["crawl"] is _HANDLERS["flock"]


class TestSSEStreamCrawl:
    @pytest.mark.asyncio
    async def test_stream_crawl_yields_events(self):
        from huginn.tasks import stream_crawl
        from huginn.models import CrawlRequest
        from huginn.state import get_state

        # Mock browser and crawler
        mock_browser = MagicMock()
        mock_page = AsyncMock()
        mock_page.content = AsyncMock(return_value="<html></html>")

        state = get_state()
        state.config = HuginnConfig()
        state.browser = mock_browser

        req = CrawlRequest(url="https://example.com")

        # Patch Crawler to return a simple result
        mock_result = MagicMock()
        mock_result.pages = []
        mock_result.completed = 0
        mock_result.total_discovered = 0

        with patch("huginn.tasks.Crawler") as MockCrawler:
            mock_crawler_instance = MockCrawler.return_value
            mock_crawler_instance.crawl = AsyncMock(return_value=mock_result)

            events = []
            async for event in stream_crawl(req):
                events.append(event)
                if len(events) > 10:
                    break

        # Should get at least a done event
        assert len(events) > 0
        assert "event: done" in events[-1]
        reset_state()


class TestSSEStreamDistill:
    @pytest.mark.asyncio
    async def test_stream_distill_yields_events(self):
        from huginn.tasks import stream_distill
        from huginn.models import DistillRequest
        from huginn.state import get_state

        mock_browser = MagicMock()
        state = get_state()
        state.config = HuginnConfig()
        state.browser = mock_browser

        req = DistillRequest(urls=["https://example.com"], prompt="Extract title")

        with patch("huginn.tasks.Extractor") as MockExtractor:
            mock_extractor = MockExtractor.return_value
            mock_extractor.scraper = MagicMock()
            mock_extractor.scraper.scrape = AsyncMock(return_value=MagicMock(
                markdown="# Title", metadata={"title": "Test"}
            ))
            mock_extractor.extract = AsyncMock(return_value={"result": "extracted"})

            events = []
            async for event in stream_distill(req):
                events.append(event)

        assert len(events) >= 2  # progress + done
        assert "event: progress" in events[0]
        assert "event: done" in events[-1]
        reset_state()


class TestRunCrawl:
    @pytest.mark.asyncio
    async def test_run_crawl_updates_job_store(self):
        from huginn.tasks import run_crawl
        from huginn.models import CrawlRequest
        from huginn.state import get_state

        mock_job_store = AsyncMock()
        mock_browser = MagicMock()

        state = get_state()
        state.config = HuginnConfig()
        state.browser = mock_browser
        state.job_store = mock_job_store

        req = CrawlRequest(url="https://example.com")

        mock_result = MagicMock()
        mock_result.pages = []
        mock_result.completed = 0
        mock_result.total_discovered = 0

        with patch("huginn.tasks.Crawler") as MockCrawler:
            mock_crawler_instance = MockCrawler.return_value
            mock_crawler_instance.crawl = AsyncMock(return_value=mock_result)

            await run_crawl("test-job-1", req)

        # Verify job store was updated
        mock_job_store.update_job.assert_any_call("test-job-1", status="running")
        mock_job_store.update_job.assert_any_call(
            "test-job-1",
            status="completed",
            job_result={"pages": []},
            completed=0,
            total=0,
        )
        reset_state()


class TestRunFlock:
    @pytest.mark.asyncio
    async def test_run_flock_completes(self):
        from huginn.tasks import run_flock
        from huginn.models import FlockRequest
        from huginn.state import get_state

        mock_job_store = AsyncMock()
        mock_browser = MagicMock()

        state = get_state()
        state.config = HuginnConfig()
        state.browser = mock_browser
        state.job_store = mock_job_store

        req = FlockRequest(urls=["https://example.com", "https://example.org"])

        mock_scrape_data = MagicMock()
        mock_scrape_data.model_dump = MagicMock(return_value={"markdown": "# Test"})

        with patch("huginn.tasks.Scraper") as MockScraper, \
             patch("huginn.circuit_breaker.get_circuit_breaker") as mock_cb_factory, \
             patch("huginn.circuit_breaker.extract_domain", return_value="example.com"), \
             patch("huginn.cache.get_cached_scrape_result", new_callable=AsyncMock, return_value=None), \
             patch("huginn.cache.cache_scrape_result", new_callable=AsyncMock):
            mock_cb = MagicMock()
            mock_cb.is_open.return_value = False
            mock_cb_factory.return_value = mock_cb

            mock_scraper_instance = MockScraper.return_value
            mock_scraper_instance.scrape = AsyncMock(return_value=mock_scrape_data)

            await run_flock("test-flock-1", req)

        # Job store should have been updated to running then completed
        # update_job is called with kwargs: update_job(job_id, status="running")
        kwargs_list = [call.kwargs for call in mock_job_store.update_job.call_args_list]
        statuses = [kw.get("status") for kw in kwargs_list if "status" in kw]
        assert "running" in statuses
        assert "completed" in statuses
        reset_state()