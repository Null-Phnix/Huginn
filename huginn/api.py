"""
Huginn API — Firecrawl-compatible web scraping service.

Autonomous, stealth-first, self-hosted.
Built on StarSearch + Blackreach's DOM walker + mental model.

Run:
    huginn serve                    # Start with defaults
    huginn serve --port 8080        # Custom port
    huginn serve --config path     # Custom config
    uvicorn huginn.api:app         # Direct uvicorn
"""

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import StreamingResponse

from .config import HuginnConfig, load_config
from .metrics import MetricsMiddleware, get_per_endpoint_stats
from .models import (
    Action,
    FlockRequest,
    FlockResponse,
    FlockResultItem,
    CrawlRequest,
    CrawlStartResponse,
    CrawlStatusResponse,
    DistillRequest,
    DistillStartResponse,
    DistillStatusResponse,
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
    StreamCrawlResponse,
    StreamDistillResponse,
    ScheduleRequest,
    ScheduleResponse,
    ResearchRequest,
    ResearchResponse,
    ResearchCitation,
    WatchRequest,
    WatchResponse,
    WatchStatusResponse,
    WatchSnapshot,
    GraphRequest,
    GraphResponse,
)
from .job_store import JobStore
from .scheduler import Scheduler
from .webhook import fire_webhook_for_job
from .browser import BrowserManager
from .scraper import Scraper
from .crawler import Crawler
from .mapper import Mapper
from .extractor import Extractor
from .searcher import Searcher

logger = logging.getLogger(__name__)

# SSE format helper — avoids f-string issues with newlines
_SSE_TEMPLATE = "event: {}\ndata: {}\n\n"


def sse_event(event: str, data: dict) -> str:
    """Format an SSE event: event line + data line + blank line."""
    return _SSE_TEMPLATE.format(event, json.dumps(data))


def _map_exception_to_error_code(e: Exception) -> Optional[str]:
    """Map exception type to Huginn ErrorCode string."""
    from .models import ErrorCode
    name = type(e).__name__.lower()
    mapping = {
        "httpx.timeout": ErrorCode.TIMEOUT,
        "httpx.connecterror": ErrorCode.CONNECTION_ERROR,
        "asyncio.timeouterror": ErrorCode.TIMEOUT,
        "circuit openerror": ErrorCode.CIRCUIT_OPEN,
        "valueerror": ErrorCode.INVALID_URL,
        "urllib.error.httperror": ErrorCode.UPSTREAM_ERROR,
        "playwright.timeout": ErrorCode.TIMEOUT,
    }
    for key, code in mapping.items():
        if key in name:
            return code
    return ErrorCode.NAVIGATION_FAILED


# ─── Rate Limiter & Helpers ─────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address)

def build_proxy_dict(config: HuginnConfig) -> Optional[dict]:
    """Build proxy dict from config for Playwright context."""
    if not config.proxy.server:
        return None
    proxy = {"server": config.proxy.server}
    if config.proxy.username:
        proxy["username"] = config.proxy.username
    if config.proxy.password:
        proxy["password"] = config.proxy.password
    return proxy


# ─── Global State ─────────────────────────────────────────────────────────────

_config: Optional[HuginnConfig] = None
_browser: Optional[BrowserManager] = None
_job_store: Optional[JobStore] = None
_crawl_tasks: dict = {}  # job_id -> asyncio.Task
_scheduler: Optional[Scheduler] = None
_watcher: Optional[Any] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    global _config, _browser, _job_store, _scheduler

    _config = app.state.config
    logging.basicConfig(level=getattr(logging, _config.log_level), format="%(asctime)s %(name)s %(levelname)s %(message)s")

    # Ensure data directory exists (moved out of __post_init__ to avoid import-time side effects)
    _config.ensure_data_dir()

    # Initialize job store
    _job_store = JobStore(_config.db_path)
    await _job_store.init()

    # Initialize browser — pass the full BrowserConfig so BrowserManager
    # can select the StarSearch backend when configured.
    _browser = BrowserManager(config=_config.browser)
    await _browser.start()
    _scheduler = Scheduler(_job_store)
    _scheduler.start()
    logger.info("Scheduler started")
    _register_scheduler_handlers()

    # Initialize page watcher (shared singleton for all watch endpoints)
    from .watcher import PageWatcher, get_watch_store
    global _watcher
    _watcher = PageWatcher(_browser, get_watch_store())
    logger.info("Page watcher initialized")

    logger.info(f"Huginn started on {_config.server.host}:{_config.server.port}")
    logger.info(f"Browser: backend={_browser.backend}, headless={_config.browser.headless}, stealth={_config.browser.stealth_mode}")

    yield

    # Cleanup
    logger.info("Shutting down Huginn...")
    for task in _crawl_tasks.values():
        task.cancel()
    # Stop all watch monitoring tasks
    if _watcher:
        for url, task in list(_watcher._monitor_tasks.items()):
            task.cancel()
            logger.info(f"Stopped monitoring {url}")
    await _browser.stop()
    await _job_store.close()
    if _scheduler:
        await _scheduler.stop()
        logger.info("Scheduler stopped")


def create_app(config: Optional[HuginnConfig] = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if config is None:
        config = HuginnConfig()

    app = FastAPI(
        title="Huginn",
        description="Autonomous web scraping API — Firecrawl-compatible, stealth-first, self-hosted",
        version="1.1.0",
        lifespan=lifespan,
        openapi_tags=[
            {"name": "Health", "description": "Server health, readiness, and metrics"},
            {"name": "Scrape", "description": "Single-page scraping in multiple formats (markdown, HTML, links, screenshot)"},
            {"name": "Crawl", "description": "Recursive site crawling with depth limits and job tracking"},
            {"name": "Map", "description": "URL discovery and site mapping"},
            {"name": "Extract", "description": "Structured data extraction with templates or LLM prompts"},
            {"name": "Search", "description": "Web search and search-driven scraping"},
            {"name": "Batch", "description": "Batch / flock operations for multiple URLs"},
            {"name": "Watch", "description": "Page change detection and monitoring"},
            {"name": "Schedule", "description": "Scheduled / recurring scraping jobs"},
            {"name": "Research", "description": "Deep autonomous research with memory and citations"},
            {"name": "Memory", "description": "ChromaDB research memory query and management"},
            {"name": "Templates", "description": "Extraction template registry"},
            {"name": "Jobs", "description": "Job lifecycle management (list, cancel)"},
        ],
    )

    app.state.config = config

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Metrics middleware (must be added before routes)
    app.add_middleware(MetricsMiddleware)

    # Rate limiting
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # ─--- Auth ─────────────────────────────────────────────────────────────

    async def verify_api_key(authorization: Optional[str] = Header(None)):
        """Optional API key verification."""
        if not config.server.api_key:
            return True
        if not authorization:
            raise HTTPException(status_code=401, detail="Missing Authorization header")
        token = authorization.removeprefix("Bearer ").strip()
        if token != config.server.api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return True

    # ─--- Health ───────────────────────────────────────────────────────────

    @app.get("/health", tags=["Health"])
    async def health():
        """Comprehensive health check endpoint."""
        return {
            "status": "ok",
            "version": "1.1.0",
            "browser": "running" if _browser else "stopped",
            "scheduler": "running" if (_scheduler and _scheduler._running) else "stopped",
        }

    @app.get("/health/detailed", tags=["Health"])
    async def health_detailed():
        """Detailed health check with circuit breaker and cache statistics."""
        from .cache import get_response_cache
        from .circuit_breaker import get_circuit_breaker, extract_domain

        cache = await get_response_cache()
        cb = get_circuit_breaker()

        return {
            "status": "ok",
            "version": "1.1.0",
            "browser": "running" if _browser else "stopped",
            "scheduler": "running" if (_scheduler and _scheduler._running) else "stopped",
            "circuit_breaker": await cb.get_stats(),
            "cache": await cache.stats(),
        }

    @app.get("/health/ready", tags=["Health"])
    async def readiness():
        """Kubernetes readiness probe — returns 503 if not ready."""
        if not _browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")
        return {"ready": True}

    @app.get("/health/live", tags=["Health"])
    async def liveness():
        """Kubernetes liveness probe."""
        return {"alive": True}

    # ─── Metrics ───────────────────────────────────────────────────────────────

    @app.get("/v1/metrics", tags=["Health"])
    async def metrics():
        """Return per-endpoint metrics: call count, avg latency, success rate."""
        return get_per_endpoint_stats()

    # ─--- Scrape ────────────────────────────────────────────────────────────

    @app.post("/v1/probe", response_model=ScrapeResponse, tags=["Scrape"])
    @limiter.limit("100/minute")
    async def scrape(request: Request, req: ScrapeRequest, auth=Depends(verify_api_key)):
        """Scrape a single URL and return content in requested formats."""
        if not _browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")

        from .circuit_breaker import get_circuit_breaker, CircuitOpenError as CBCircuitOpen, extract_domain
        from .cache import get_cached_scrape_result, cache_scrape_result
        from .models import ErrorCode

        cb = get_circuit_breaker()
        domain = extract_domain(req.url)

        # Check circuit breaker first
        is_open = cb.is_open(domain)
        if is_open:
            return ScrapeResponse(
                success=False,
                error=f"Circuit breaker open for {domain}. Domain is temporarily blocked due to repeated failures.",
                error_code=ErrorCode.CIRCUIT_OPEN,
            )

        # Check response cache
        formats = req.formats or [OutputFormat.MARKDOWN]
        cached = await get_cached_scrape_result(req.url, formats)
        if cached:
            return ScrapeResponse(success=True, data=cached, cached=True)

        scraper = Scraper(_browser, cb)
        proxy_dict = build_proxy_dict(config)

        try:
            data = await scraper.scrape(
                url=req.url,
                formats=formats,
                headers=req.headers,
                wait_for=req.wait_for,
                actions=[a.model_dump() for a in req.actions] if req.actions else None,
                include_tags=req.include_tags,
                exclude_tags=req.exclude_tags,
                only_main_content=req.only_main_content,
                timeout=req.timeout,
                proxy=proxy_dict,
                max_retries=req.max_retries,
                scroll=req.scroll,
                render_mode=req.render_mode,
            )
            # Cache successful result
            await cache_scrape_result(req.url, formats, data)
            # Record success for circuit breaker
            await cb.record_success(domain)
            return ScrapeResponse(success=True, data=data)
        except CBCircuitOpen:
            return ScrapeResponse(
                success=False,
                error=f"Circuit breaker opened for {domain} during request.",
                error_code=ErrorCode.CIRCUIT_OPEN,
            )
        except asyncio.TimeoutError:
            await cb.record_failure(domain)
            return ScrapeResponse(
                success=False,
                error=f"Request timed out after {req.timeout}ms",
                error_code=ErrorCode.TIMEOUT,
            )
        except Exception as e:
            await cb.record_failure(domain)
            error_code = _map_exception_to_error_code(e)
            return ScrapeResponse(success=False, error=str(e), error_code=error_code)

    # ─--- Crawl ─────────────────────────────────────────────────────────────

    @app.post("/v1/sweep", tags=["Crawl"])
    async def start_sweep(req: CrawlRequest, auth=Depends(verify_api_key)):
        """Start an async crawl job. Returns job ID for polling, or streams if requested."""
        if not _browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")

        if req.stream:
            if req.format == "jsonl":
                return StreamingResponse(
                    _jsonl_stream_crawl(req),
                    media_type="application/x-ndjson",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                )
            # Default: SSE
            return StreamingResponse(
                _stream_crawl(req),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        if not _job_store:
            raise HTTPException(status_code=503, detail="Job store not initialized")

        # Create job
        job_id = await _job_store.create_job(
            "sweep",
            req.model_dump(exclude_none=True),
            ttl=config.server.job_ttl,
        )

        # Start background crawl
        task = asyncio.create_task(_run_crawl(job_id, req))
        _crawl_tasks[job_id] = task

        return CrawlStartResponse(success=True, id=job_id, url=f"/v1/sweep/{job_id}")

    @app.get("/v1/sweep/{job_id}", response_model=CrawlStatusResponse, tags=["Crawl"])
    @limiter.limit("100/minute")
    async def get_sweep_status(request: Request, job_id: str, auth=Depends(verify_api_key)):
        """Get crawl job status and results."""
        if not _job_store:
            raise HTTPException(status_code=503, detail="Job store not initialized")

        job = await _job_store.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        status = JobStatus(job["status"])
        result_data = None
        if status == JobStatus.COMPLETED and job.get("result_json"):
            result = json.loads(job["result_json"])
            result_data = [ScrapeData(**page) if isinstance(page, dict) else page for page in result.get("pages", [])]

        # Parse expires_at from job store if available
        expires_at = None
        if job.get("expires_at"):
            try:
                from datetime import datetime, timezone
                expires_at = dt.fromisoformat(job["expires_at"])
            except (ValueError, TypeError):
                pass

        return CrawlStatusResponse(
            success=True,
            status=status,
            completed=job.get("completed", 0),
            total=job.get("total"),
            expires_at=expires_at,
            data=result_data,
            error=job.get("error"),
        )

    @app.delete("/v1/sweep/{job_id}", tags=["Crawl"])
    @limiter.limit("100/minute")
    async def cancel_crawl(request: Request, job_id: str, auth=Depends(verify_api_key)):
        """Cancel a running crawl job."""
        if job_id in _crawl_tasks:
            _crawl_tasks[job_id].cancel()
            del _crawl_tasks[job_id]
        if _job_store:
            await _job_store.update_job(job_id, status="cancelled")
        return {"success": True}

    # ─--- Map ──────────────────────────────────────────────────────────────

    @app.post("/v1/chart", response_model=MapResponse, tags=["Map"])
    @limiter.limit("60/minute")
    async def chart_site(request: Request, req: MapRequest, auth=Depends(verify_api_key)):
        """Fast URL discovery — returns all links without full content extraction."""
        if not _browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")

        mapper = Mapper(_browser)
        try:
            links = await mapper.chart_site(
                url=req.url,
                search=req.search,
                include_subdomains=req.include_subdomains,
                limit=req.limit,
            )
            return MapResponse(success=True, links=links)
        except Exception as e:
            logger.error(f"Map failed: {e}", exc_info=True)
            return MapResponse(success=False, error=str(e))

    @app.post("/v1/graph", response_model=GraphResponse, tags=["Map"])
    @limiter.limit("30/minute")
    async def graph_site(request: Request, req: GraphRequest, auth=Depends(verify_api_key)):
        """
        Map a site as a directed graph of pages and links.

        BFS crawl up to max_depth, returning nodes (pages with metadata)
        and edges (source -> target links). Useful for site architecture
        analysis, link visualization, and audit.
        """
        if not _browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")

        mapper = Mapper(_browser)
        try:
            graph = await mapper.map_site_graph(
                start_url=req.url,
                include_subdomains=req.include_subdomains,
                limit=req.limit,
                max_depth=req.max_depth,
            )
            return GraphResponse(success=True, data=graph)
        except Exception as e:
            logger.error(f"Graph failed: {e}", exc_info=True)
            return GraphResponse(success=False, error=str(e), error_code=ErrorCode.INTERNAL_ERROR)

    @app.post("/v1/distill", tags=["Extract"])
    async def start_distill(req: DistillRequest, auth=Depends(verify_api_key)):
        """Start an async extraction job. Returns job ID for polling, or SSE stream if stream=True."""
        if not _browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")

        if req.stream:
            return StreamingResponse(
                _stream_distill(req),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        if not _job_store:
            raise HTTPException(status_code=503, detail="Job store not initialized")

        job_id = await _job_store.create_job(
            "extract",
            req.model_dump(exclude_none=True),
            ttl=config.server.job_ttl,
        )

        task = asyncio.create_task(_run_distill(job_id, req))
        _crawl_tasks[job_id] = task

        return DistillStartResponse(success=True, id=job_id)

    @app.get("/v1/distill/{job_id}", response_model=DistillStatusResponse, tags=["Extract"])
    @limiter.limit("100/minute")
    async def get_distill_status(request: Request, job_id: str, auth=Depends(verify_api_key)):
        """Get extraction job status and results."""
        if not _job_store:
            raise HTTPException(status_code=503, detail="Job store not initialized")

        job = await _job_store.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        status = JobStatus(job["status"])
        result_data = None
        if status == JobStatus.COMPLETED and job.get("result_json"):
            result_data = json.loads(job["result_json"])

        return DistillStatusResponse(
            success=True,
            status=status,
            data=result_data,
            error=job.get("error"),
        )

    # ─── Deep Research ─────────────────────────────────────────────────────

    @app.post("/v1/research", response_model=ResearchResponse, tags=["Research"])
    async def deep_research(req: ResearchRequest, auth=Depends(verify_api_key)):
        """
        Conduct autonomous deep research on any topic.

        Iteratively explores multiple sources, tracks beliefs with confidence
        scores, detects contradictions, and synthesizes a structured report.

        This is Huginn's most powerful endpoint — far beyond what Firecrawl
        or any single-pass scraper can achieve.
        """
        if not _browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")

        from .memory import ResearchMemory
        from .researcher import DeepResearcher

        try:
            memory = ResearchMemory(data_dir=config.data_dir) if config.server.data_dir else None
            researcher = DeepResearcher(
                browser=_browser,
                llm_provider=_config.extract.llm_provider,
                llm_model=_config.extract.llm_model,
                urls=req.urls,
                memory=memory,
            )

            result = await researcher.research(
                query=req.query,
                depth=req.depth,
                max_sources=req.max_sources,
                target_length=req.target_length,
                background_questions=req.background_questions,
            )

            return ResearchResponse(
                success=True,
                query=result.query,
                summary=result.summary,
                report=result.report,
                findings=[
                    ResearchFinding(
                        topic=f.topic,
                        claim=f.claim,
                        supporting_citations=[
                            ResearchCitation(**c.to_dict()) for c in f.supporting_citations
                        ],
                        confidence=f.confidence,
                        contradicts=f.contradicts,
                        needs_verification=f.needs_verification,
                        verified=f.verified,
                    )
                    for f in result.findings
                ],
                citations=[ResearchCitation(**c.to_dict()) for c in result.citations],
                confidence=result.confidence,
                sources_consulted=result.sources_consulted,
                research_duration_seconds=result.research_duration_seconds,
                depth_achieved=result.depth_achieved,
                warnings=result.warnings,
            )

        except Exception as e:
            logger.error(f"Deep research failed: {e}", exc_info=True)
            return ResearchResponse(
                success=False,
                error=str(e),
                error_code=_map_exception_to_error_code(e),
            )

    # ─── Search ────────────────────────────────────────────────────────────

    @app.post("/v1/seek", response_model=SearchResponse, tags=["Search"])
    @limiter.limit("30/minute")
    async def search(request: Request, req: SearchRequest, auth=Depends(verify_api_key)):
        """Search the web and scrape results."""
        if not _browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")

        searcher = Searcher(_browser, fallback_chain=req.fallback_chain)
        formats = []
        if req.scrape_options:
            formats = req.scrape_options.formats

        try:
            results = await searcher.search(
                query=req.query,
                limit=req.search_options.limit if req.search_options else 5,
                scrape_formats=formats or [OutputFormat.MARKDOWN],
                tbs=req.search_options.tbs if req.search_options else None,
                country=req.search_options.country if req.search_options else None,
                language=req.search_options.language if req.search_options else None,
                scrape_results=req.scrape_results,
            )
            return SearchResponse(success=True, data=results)
        except Exception as e:
            logger.error(f"Search failed: {e}", exc_info=True)
            return SearchResponse(success=False, error=str(e))

    # ─--- Jobs ─────────────────────────────────────────────────────────────

    @app.get("/v1/jobs", tags=["Jobs"])
    @limiter.limit("100/minute")
    async def list_jobs(
        request: Request,
        status: Optional[str] = Query(None),
        limit: int = Query(50, le=200),
        auth=Depends(verify_api_key),
    ):
        """List all jobs, optionally filtered by status."""
        if not _job_store:
            raise HTTPException(status_code=503, detail="Job store not initialized")
        jobs = await _job_store.list_jobs(status=status, limit=limit)
        return {"success": True, "jobs": jobs}

    @app.delete("/v1/jobs/{job_id}", tags=["Jobs"])
    @limiter.limit("100/minute")
    async def delete_job(request: Request, job_id: str, auth=Depends(verify_api_key)):
        """Delete a job."""
        if not _job_store:
            raise HTTPException(status_code=503, detail="Job store not initialized")
        deleted = await _job_store.delete_job(job_id)
        return {"success": deleted}

    # ─--- Batch Scrape ──────────────────────────────────────────────────────────

    @app.post("/v1/flock", response_model=FlockResponse, tags=["Batch"])
    @limiter.limit("10/minute")
    async def flock_scrape(request: Request, req: FlockRequest, auth=Depends(verify_api_key)):
        """Scrape multiple URLs concurrently with partial results support."""
        if not _browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")

        from .circuit_breaker import get_circuit_breaker, extract_domain
        from .cache import get_cached_scrape_result, cache_scrape_result
        from .models import ErrorCode

        proxy_dict = build_proxy_dict(config)
        cb = get_circuit_breaker()
        scraper = Scraper(_browser, cb)
        sem = asyncio.Semaphore(5)
        results: List[FlockResultItem] = []
        warnings: List[str] = []

        async def scrape_one(url: str) -> FlockResultItem:
            async with sem:
                domain = extract_domain(url)

                # Skip circuit-open domains
                if cb.is_open(domain):
                    warnings.append(f"Skipped {url}: circuit breaker open")
                    return FlockResultItem(
                        url=url, success=False,
                        error=f"Circuit breaker open for {domain}",
                        error_code=ErrorCode.CIRCUIT_OPEN,
                    )

                # Check cache
                cached = await get_cached_scrape_result(url, req.formats or [OutputFormat.MARKDOWN])
                if cached:
                    return FlockResultItem(url=url, success=True, data=cached, cached=True)

                try:
                    data = await scraper.scrape(
                        url=url,
                        formats=req.formats,
                        include_tags=req.include_tags,
                        exclude_tags=req.exclude_tags,
                        only_main_content=req.only_main_content,
                        timeout=req.timeout,
                        proxy=proxy_dict,
                    )
                    await cache_scrape_result(url, req.formats or [OutputFormat.MARKDOWN], data)
                    await cb.record_success(domain)
                    return FlockResultItem(url=url, success=True, data=data)
                except asyncio.TimeoutError:
                    await cb.record_failure(domain)
                    return FlockResultItem(
                        url=url, success=False,
                        error=f"Request timed out after {req.timeout}ms",
                        error_code=ErrorCode.TIMEOUT,
                    )
                except Exception as e:
                    await cb.record_failure(domain)
                    return FlockResultItem(
                        url=url, success=False,
                        error=str(e),
                        error_code=_map_exception_to_error_code(e),
                    )

        tasks = [scrape_one(url) for url in req.urls]
        items = await asyncio.gather(*tasks, return_exceptions=True)

        for i, item in enumerate(items):
            if isinstance(item, FlockResultItem):
                results.append(item)
            elif isinstance(item, Exception):
                results.append(FlockResultItem(
                    url=req.urls[i] if i < len(req.urls) else "unknown",
                    success=False, error=str(item),
                    error_code=ErrorCode.NAVIGATION_FAILED,
                ))

        success_count = sum(1 for r in results if r.success)
        partial = success_count > 0 and success_count < len(results)

        return FlockResponse(
            success=success_count > 0,
            partial=partial,
            data=results,
            warnings=warnings if warnings else None,
        )

    # ─── Page Watch / Change Detection ─────────────────────────────────────

    from .watcher import PageWatcher, get_watch_store, compute_content_hash
    from .models import ErrorCode as EC

    @app.post("/v1/watch", response_model=WatchResponse, tags=["Watch"])
    async def watch_page(req: WatchRequest, auth=Depends(verify_api_key)):
        """
        Start watching a page for content changes.

        Takes an initial snapshot and returns the content hash.
        Use GET /v1/watch/{url} to check status,
        and DELETE /v1/watch/{url} to stop watching.
        """
        if not _browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")

        from .watcher import extract_domain

        domain = extract_domain(req.url)
        cb = get_circuit_breaker()

        if cb.is_open(domain):
            return WatchResponse(
                success=False,
                url=req.url,
                domain=domain,
                content_hash="",
                error=f"Circuit breaker open for {domain}",
                error_code=EC.CIRCUIT_OPEN,
            )

        store = get_watch_store()

        try:
            # Take initial snapshot
            snapshot = await _watcher.check(req.url)
        except Exception as e:
            await cb.record_failure(domain)
            return WatchResponse(
                success=False,
                url=req.url,
                domain=domain,
                content_hash="",
                error=str(e),
                error_code=_map_exception_to_error_code(e),
            )

        await cb.record_success(domain)

        # Register watching
        entry = await store.watch(
            url=req.url,
            selectors=req.selectors,
            webhook_url=req.webhook_url,
        )
        await store.add_snapshot(req.url, snapshot)

        # Start background monitoring if interval is set
        if req.check_interval_seconds >= 60:
            await _watcher.start_monitoring(req.url, req.check_interval_seconds)

        return WatchResponse(
            success=True,
            url=req.url,
            domain=domain,
            content_hash=snapshot.content_hash,
            change_count=0,
            last_check=snapshot.created_at,
            message="Now watching for changes. Webhook will fire on content changes.",
        )

    @app.get("/v1/watch/{url:path}", response_model=WatchStatusResponse, tags=["Watch"])
    async def get_watch_status(url: str, auth=Depends(verify_api_key)):
        """Get the current status and history of a watched page."""
        from .watcher import extract_domain

        store = get_watch_store()
        entry = await store.get(url)

        if not entry:
            raise HTTPException(status_code=404, detail="URL is not being watched")

        return WatchStatusResponse(
            success=True,
            url=entry.url,
            domain=entry.domain,
            enabled=entry.enabled,
            webhook_url=entry.webhook_url,
            selectors=entry.selectors,
            snapshot_count=len(entry.snapshots),
            change_count=entry.change_count,
            last_check=entry.last_check,
            last_change=entry.last_change,
            latest_hash=entry.latest_snapshot().content_hash if entry.latest_snapshot() else None,
            history=[
                WatchSnapshot(
                    content_hash=s.content_hash,
                    detected_changes=s.detected_changes,
                    created_at=s.created_at,
                )
                for s in entry.snapshots
            ],
        )

    @app.post("/v1/watch/{url:path}/check", response_model=WatchResponse, tags=["Watch"])
    async def check_watch(url: str, auth=Depends(verify_api_key)):
        """
        Manually trigger a check for a watched URL.
        Returns the new snapshot and any detected changes.
        """
        from .watcher import extract_domain

        cb = get_circuit_breaker()
        domain = extract_domain(url)
        store = get_watch_store()

        entry = await store.get(url)
        if not entry:
            raise HTTPException(status_code=404, detail="URL is not being watched")

        try:
            snapshot = await _watcher.check_and_notify(url)
        except Exception as e:
            await cb.record_failure(domain)
            return WatchResponse(
                success=False,
                url=url,
                domain=domain,
                content_hash="",
                error=str(e),
                error_code=_map_exception_to_error_code(e),
            )

        return WatchResponse(
            success=True,
            url=url,
            domain=domain,
            content_hash=snapshot.content_hash,
            change_count=entry.change_count,
            last_check=snapshot.created_at,
            last_change=entry.last_change,
            message="No changes detected" if not snapshot.detected_changes else f"Detected {len(snapshot.detected_changes)} change(s)",
        )

    @app.delete("/v1/watch/{url:path}", tags=["Watch"])
    async def unwatch_page(url: str, auth=Depends(verify_api_key)):
        """Stop watching a page."""
        store = get_watch_store()
        await _watcher.stop_monitoring(url)
        removed = await store.unwatch(url)

        return {"success": removed, "url": url}

    @app.get("/v1/watches", tags=["Watch"])
    async def list_watches(auth=Depends(verify_api_key)):
        """List all watched URLs."""
        store = get_watch_store()
        entries = await store.list_watched()
        return {
            "success": True,
            "count": len(entries),
            "watches": [e.to_dict() for e in entries],
        }


    # ─── Schedule Endpoints ────────────────────────────────────────────────

    @app.post("/v1/schedule", response_model=ScheduleResponse, tags=["Schedule"])
    async def create_schedule(req: ScheduleRequest, auth=Depends(verify_api_key)):
        """Create a new crawl/distill schedule."""
        import uuid
        if not req.cron and not req.interval_seconds:
            raise HTTPException(status_code=400, detail="Provide cron or interval_seconds")
        
        schedule_id = str(uuid.uuid4())
        
        # Register with scheduler (handles DB persistence internally)
        if req.enabled:
            schedule = await _scheduler.add_schedule(
                name=req.name,
                job_type=req.job_type,
                request={"job_type": req.job_type, "request": req.request, "webhook_url": req.webhook_url},
                cron=req.cron,
                interval_seconds=req.interval_seconds,
                webhook_url=req.webhook_url,
                enabled=True,
            )
            schedule_id = schedule["id"]
        
        logger.info(f"Created schedule {schedule_id} ({req.name})")
        
        return ScheduleResponse(
            id=schedule_id,
            name=req.name,
            job_type=req.job_type,
            cron=req.cron,
            interval_seconds=req.interval_seconds,
            request_json=json.dumps(req.request),
            webhook_url=req.webhook_url,
            enabled=req.enabled,
            status="active",
            created_at=datetime.now(timezone.utc),
        )

    @app.get("/v1/schedule", response_model=List[ScheduleResponse], tags=["Schedule"])
    async def list_schedules(auth=Depends(verify_api_key)):
        """List all schedules."""
        schedules = await _scheduler.list_schedules()
        return [
            ScheduleResponse(
                id=s["id"],
                name=s["name"],
                job_type=s["job_type"],
                cron=s.get("cron"),
                interval_seconds=s.get("interval_seconds"),
                request_json=s.get("request", ""),
                webhook_url=s.get("webhook_url"),
                enabled=s.get("enabled", True),
                status="active" if s.get("enabled") else "paused",
                created_at=s.get("created_at"),
                last_run=s.get("last_run"),
                next_run=s.get("next_run"),
            )
            for s in schedules
        ]

    @app.get("/v1/schedule/{schedule_id}", response_model=ScheduleResponse, tags=["Schedule"])
    async def get_schedule(schedule_id: str, auth=Depends(verify_api_key)):
        """Get a specific schedule."""
        s = await _scheduler.get_schedule(schedule_id)
        if not s:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return ScheduleResponse(
            id=s["id"],
            name=s["name"],
            job_type=s["job_type"],
            cron=s.get("cron"),
            interval_seconds=s.get("interval_seconds"),
            request_json=s.get("request", ""),
            webhook_url=s.get("webhook_url"),
            enabled=s.get("enabled", True),
            status="active" if s.get("enabled") else "paused",
            created_at=s.get("created_at"),
            last_run=s.get("last_run"),
            next_run=s.get("next_run"),
        )

    @app.delete("/v1/schedule/{schedule_id}", tags=["Schedule"])
    async def delete_schedule(schedule_id: str, auth=Depends(verify_api_key)):
        """Delete a schedule."""
        await _scheduler.delete_schedule(schedule_id)
        return {"success": True}

    @app.post("/v1/schedule/{schedule_id}/pause", tags=["Schedule"])
    async def pause_schedule(schedule_id: str, auth=Depends(verify_api_key)):
        """Pause a schedule."""
        result = await _scheduler.pause_schedule(schedule_id)
        return {"success": True}

    @app.post("/v1/schedule/{schedule_id}/resume", tags=["Schedule"])
    async def resume_schedule(schedule_id: str, auth=Depends(verify_api_key)):
        """Resume a schedule."""
        result = await _scheduler.resume_schedule(schedule_id)
        return {"success": True}

    # ─── Firecrawl-compatible route aliases ─────────────────────────────────────

    @app.post("/v1/scrape", response_model=ScrapeResponse, tags=["Scrape"])
    async def scrape_alias(request: Request, req: ScrapeRequest, auth=Depends(verify_api_key)):
        return await scrape(request, req, auth)

    @app.post("/v1/crawl", tags=["Crawl"])
    async def crawl_alias(req: CrawlRequest, auth=Depends(verify_api_key)):
        return await start_sweep(req, auth)

    @app.get("/v1/crawl/{job_id}", tags=["Crawl"])
    async def crawl_status_alias(request: Request, job_id: str, auth=Depends(verify_api_key)):
        return await get_sweep_status(request, job_id, auth)

    @app.delete("/v1/crawl/{job_id}", tags=["Crawl"])
    async def crawl_cancel_alias(request: Request, job_id: str, auth=Depends(verify_api_key)):
        return await cancel_crawl(request, job_id, auth)

    @app.post("/v1/map", response_model=MapResponse, tags=["Map"])
    async def map_alias(request: Request, req: MapRequest, auth=Depends(verify_api_key)):
        return await chart_site(request, req, auth)

    @app.post("/v1/extract", tags=["Extract"])
    async def extract_alias(req: DistillRequest, auth=Depends(verify_api_key)):
        return await start_distill(req, auth)

    @app.get("/v1/extract/{job_id}", tags=["Extract"])
    async def extract_status_alias(request: Request, job_id: str, auth=Depends(verify_api_key)):
        return await get_distill_status(request, job_id, auth)

    @app.post("/v1/search", response_model=SearchResponse, tags=["Search"])
    async def search_alias(request: Request, req: SearchRequest, auth=Depends(verify_api_key)):
        return await search(request, req, auth)

    # ─── Firecrawl Batch Endpoint Aliases ─────────────────────────────────────────

    @app.post("/v1/batch/scrape", response_model=FlockResponse, tags=["Batch"])
    async def batch_scrape_alias(request: Request, req: FlockRequest, auth=Depends(verify_api_key)):
        """Firecrawl-compatible alias for /v1/flock."""
        return await flock_scrape(request, req, auth)

    @app.get("/v1/batch/scrape/{job_id}", tags=["Batch"])
    async def batch_scrape_status_alias(job_id: str, auth=Depends(verify_api_key)):
        """Firecrawl-compatible alias for batch scrape status — maps to job store."""
        if not _job_store:
            raise HTTPException(status_code=503, detail="Job store not initialized")
        job = await _job_store.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return {
            "success": True,
            "job_id": job_id,
            "status": job.get("status"),
            "completed": job.get("completed"),
            "total": job.get("total"),
            "error": job.get("error"),
        }

    # ─── Firecrawl Crawl Cancel POST Alias ─────────────────────────────────────────

    @app.post("/v1/crawl/{job_id}/cancel", tags=["Crawl"])
    async def crawl_cancel_post_alias(request: Request, job_id: str, auth=Depends(verify_api_key)):
        """Firecrawl-compatible alias — POST to cancel a crawl."""
        return await cancel_crawl(request, job_id, auth)


# ─── Templates API ─────────────────────────────────────────────────────────────
# ─── Templates API ─────────────────────────────────────────────────────────────

    @app.get("/v1/templates", tags=["Templates"])
    async def list_templates_api(auth=Depends(verify_api_key)):
        """List all available extraction templates with schemas."""
        from .templates import get_all_templates
        result = []
        for name, t in get_all_templates().items():
            result.append({
                "name": name,
                "description": t.description,
                "schema": t.schema,
                "fields_guide": t.fields_guide,
                "merge_strategy": t.merge_strategy,
                "max_page_chars": t.max_page_chars,
            })
        return {"success": True, "templates": result, "count": len(result)}

    @app.get("/v1/templates/{template_name}", tags=["Templates"])
    async def get_template_api(template_name: str, auth=Depends(verify_api_key)):
        """Get a single template's full details."""
        from .templates import get_template
        try:
            t = get_template(template_name)
            return {
                "success": True,
                "name": t.name,
                "description": t.description,
                "schema": t.schema,
                "system_prompt": t.system_prompt,
                "fields_guide": t.fields_guide,
                "merge_strategy": t.merge_strategy,
                "max_page_chars": t.max_page_chars,
            }
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"Template '{template_name}' not found"
            )

# ─── Research Memory API ───────────────────────────────────────────────────────

    @app.get("/v1/memory/query", tags=["Memory"])
    async def memory_query(
        q: str = Query(..., description="Query text"),
        n: int = Query(5, ge=1, le=50),
        min_relevance: float = Query(0.0, ge=0.0, le=1.0),
        type: Optional[str] = Query(None, description="Filter by type: finding, citation, snippet, report_summary"),
        auth=Depends(verify_api_key),
    ):
        """Semantic search over accumulated research memory."""
        from .memory import ResearchMemory

        memory = ResearchMemory(data_dir=config.data_dir)
        if not memory.available:
            raise HTTPException(status_code=503, detail="Research memory not available (chromadb not installed)")

        results = await memory.query(
            query_text=q,
            n_results=n,
            min_relevance=min_relevance,
            filter_type=type,
        )

        return {
            "success": True,
            "query": q,
            "count": len(results),
            "results": results,
        }


    @app.get("/v1/memory/reports", tags=["Memory"])
    async def memory_reports(auth=Depends(verify_api_key)):
        """List all stored research reports."""
        from .memory import ResearchMemory

        memory = ResearchMemory(data_dir=config.data_dir)
        if not memory.available:
            raise HTTPException(status_code=503, detail="Research memory not available")

        reports = await memory.get_all_reports()
        return {"success": True, "reports": reports, "count": len(reports)}


    @app.delete("/v1/memory/reports/{report_id}", tags=["Memory"])
    async def memory_delete_report(report_id: str, auth=Depends(verify_api_key)):
        """Delete a research report and all its associated findings/citations."""
        from .memory import ResearchMemory

        memory = ResearchMemory(data_dir=config.data_dir)
        if not memory.available:
            raise HTTPException(status_code=503, detail="Research memory not available")

        await memory.delete_report(report_id)
        return {"success": True, "deleted": report_id}


    @app.get("/v1/memory/related", tags=["Memory"])
    async def memory_related(
        topic: str = Query(...),
        n: int = Query(10, ge=1, le=50),
        auth=Depends(verify_api_key),
    ):
        """Find topics related to the given topic from accumulated research."""
        from .memory import ResearchMemory

        memory = ResearchMemory(data_dir=config.data_dir)
        if not memory.available:
            raise HTTPException(status_code=503, detail="Research memory not available")

        topics = await memory.get_related_topics(topic, n_results=n)
        return {"success": True, "topic": topic, "related": topics, "count": len(topics)}


    return app



async def _schedule_handler(request: dict):
    """Handle a scheduled job firing. Scheduler passes already-parsed dict."""
    import uuid
    job_type = request.get("job_type", "crawl")
    request_dict = request.get("request", {})
    
    job_id = str(uuid.uuid4())
    
    await _job_store.create_job({
        "id": job_id,
        "type": job_type,
        "status": "queued",
        "request": request_dict,
    })
    
    if job_type == "crawl":
        from .models import CrawlRequest
        req = CrawlRequest(**request_dict)
        await _run_crawl(job_id, req)
    elif job_type == "distill":
        from .models import DistillRequest
        req = DistillRequest(**request_dict)
        await _run_distill(job_id, req)
    elif job_type == "flock":
        from .models import FlockRequest
        req = FlockRequest(**request_dict)
        await _run_flock(job_id, req)

# Register handlers with the scheduler's registry
def _register_scheduler_handlers():
    from .scheduler import _HANDLERS
    _HANDLERS["crawl"] = _schedule_handler
    _HANDLERS["distill"] = _schedule_handler
    _HANDLERS["flock"] = _schedule_handler


# ─── Background Task Runners ────────────────────────────────────────────────

async def _run_crawl(job_id: str, req: CrawlRequest):
    """Background task for crawl jobs."""
    scraper_formats = []
    if req.scrape_options:
        scraper_formats = req.scrape_options.formats

    crawler = Crawler(
        browser=_browser,
        max_depth=req.max_depth or _config.crawl.max_depth,
        max_pages=req.limit or _config.crawl.max_pages,
        concurrency=_config.crawl.concurrency,
        delay=_config.crawl.delay_between_requests,
        allow_external=req.allow_external_links,
        allow_backward=req.allow_backward_crawling,
        include_paths=req.include_paths,
        exclude_paths=req.exclude_paths,
    )

    try:
        await _job_store.update_job(job_id, status="running")

        result = await crawler.crawl(
            start_url=req.url,
            scrape_formats=scraper_formats or [OutputFormat.MARKDOWN],
            only_main_content=req.scrape_options.only_main_content if req.scrape_options else True,
            timeout=_config.browser.navigation_timeout,
        )

        # Store results
        pages_data = []
        for page in result.pages:
            pages_data.append(page.model_dump(exclude_none=True))

        await _job_store.update_job(
            job_id,
            status="completed",
            job_result={"pages": pages_data},
            completed=result.completed,
            total=result.total_discovered,
        )

    except asyncio.CancelledError:
        await _job_store.update_job(job_id, status="cancelled", error="Job cancelled")
    except Exception as e:
        logger.error(f"Crawl job {job_id} failed: {e}", exc_info=True)
        await _job_store.update_job(job_id, status="failed", error=str(e))
    finally:
        _crawl_tasks.pop(job_id, None)


async def _run_distill(job_id: str, req: DistillRequest):
    """Background task for extract jobs."""
    import httpx
    http_client = httpx.AsyncClient(timeout=120, limits=httpx.Limits(max_connections=20))
    try:
        extractor = Extractor(
            browser=_browser,
            llm_provider=_config.extract.llm_provider,
            llm_model=_config.extract.llm_model,
            max_retries=req.max_retries,
            mental_model=req.mental_model,
            http_client=http_client,
        )

        await _job_store.update_job(job_id, status="running")

        from .templates import get_template
        template = get_template(req.template) if req.template else None

        result = await extractor.extract(
            urls=req.urls,
            prompt=req.prompt,
            schema=req.schema_,
            system_prompt=req.system_prompt,
            output_format=req.format,
            template=template,
            examples=req.examples,
        )

        await _job_store.update_job(
            job_id,
            status="completed",
            job_result=result,
        )

    except asyncio.CancelledError:
        await _job_store.update_job(job_id, status="cancelled", error="Job cancelled")
    except Exception as e:
        logger.error(f"Extract job {job_id} failed: {e}", exc_info=True)
        await _job_store.update_job(job_id, status="failed", error=str(e))
    finally:
        await http_client.aclose()
        _crawl_tasks.pop(job_id, None)


# ─── Batch Task Runner ─────────────────────────────────────────────────────────

async def _run_flock(job_id: str, req: FlockRequest):
    """Background task for batch scrape (flock) jobs."""
    from .circuit_breaker import get_circuit_breaker, extract_domain
    from .cache import get_cached_scrape_result, cache_scrape_result
    from .models import ErrorCode

    proxy_dict = build_proxy_dict(config)
    cb = get_circuit_breaker()
    scraper = Scraper(_browser, cb)
    sem = asyncio.Semaphore(5)
    results: List[FlockResultItem] = []

    async def scrape_one(url: str) -> FlockResultItem:
        async with sem:
            domain = extract_domain(url)
            if cb.is_open(domain):
                return FlockResultItem(
                    url=url, success=False,
                    error=f"Circuit breaker open for {domain}",
                    error_code=ErrorCode.CIRCUIT_OPEN,
                )

            cached = await get_cached_scrape_result(url, req.formats or [OutputFormat.MARKDOWN])
            if cached:
                return FlockResultItem(url=url, success=True, data=cached, cached=True)

            try:
                data = await scraper.scrape(
                    url=url,
                    formats=req.formats,
                    include_tags=req.include_tags,
                    exclude_tags=req.exclude_tags,
                    only_main_content=req.only_main_content,
                    timeout=req.timeout,
                    proxy=proxy_dict,
                )
                await cache_scrape_result(url, req.formats or [OutputFormat.MARKDOWN], data)
                await cb.record_success(domain)
                return FlockResultItem(url=url, success=True, data=data)
            except asyncio.TimeoutError:
                await cb.record_failure(domain)
                return FlockResultItem(
                    url=url, success=False,
                    error=f"Request timed out after {req.timeout}ms",
                    error_code=ErrorCode.TIMEOUT,
                )
            except Exception as e:
                await cb.record_failure(domain)
                return FlockResultItem(
                    url=url, success=False,
                    error=str(e),
                    error_code=_map_exception_to_error_code(e),
                )

    try:
        await _job_store.update_job(job_id, status="running")

        tasks = [scrape_one(url) for url in req.urls]
        items = await asyncio.gather(*tasks, return_exceptions=True)

        for i, item in enumerate(items):
            if isinstance(item, FlockResultItem):
                results.append(item)
            elif isinstance(item, Exception):
                results.append(FlockResultItem(
                    url=req.urls[i] if i < len(req.urls) else "unknown",
                    success=False, error=str(item),
                    error_code=ErrorCode.NAVIGATION_FAILED,
                ))

        success_count = sum(1 for r in results if r.success)
        partial = success_count > 0 and success_count < len(results)

        await _job_store.update_job(
            job_id,
            status="completed",
            job_result={"results": [r.model_dump() for r in results], "partial": partial},
            completed=success_count,
            total=len(results),
        )

        if req.webhook_url:
            await fire_webhook_for_job(req.webhook_url, job_id, "flock", results)

    except asyncio.CancelledError:
        await _job_store.update_job(job_id, status="cancelled", error="Job cancelled")
    except Exception as e:
        logger.error(f"Flock job {job_id} failed: {e}", exc_info=True)
        await _job_store.update_job(job_id, status="failed", error=str(e))
    finally:
        _crawl_tasks.pop(job_id, None)


# ─── SSE Streaming Generators ────────────────────────────────────────────────

async def _stream_crawl(req: CrawlRequest):
    """SSE generator for crawl — yields document events and a final done event."""
    scraper_formats = []
    if req.scrape_options:
        scraper_formats = req.scrape_options.formats

    crawler = Crawler(
        browser=_browser,
        max_depth=req.max_depth or _config.crawl.max_depth,
        max_pages=req.limit or _config.crawl.max_pages,
        concurrency=_config.crawl.concurrency,
        delay=_config.crawl.delay_between_requests,
        allow_external=req.allow_external_links,
        allow_backward=req.allow_backward_crawling,
        include_paths=req.include_paths,
        exclude_paths=req.exclude_paths,
    )

    try:
        # Use an asyncio.Queue to relay pages from the crawl task
        page_queue: asyncio.Queue = asyncio.Queue()

        async def _crawl_and_enqueue():
            """Run crawl and put each page into the queue as it completes."""
            try:
                result = await crawler.crawl(
                    start_url=req.url,
                    scrape_formats=scraper_formats or [OutputFormat.MARKDOWN],
                    only_main_content=req.scrape_options.only_main_content if req.scrape_options else True,
                    timeout=_config.browser.navigation_timeout,
                )
                # Put all pages in queue
                for page in result.pages:
                    await page_queue.put(("document", page))
                await page_queue.put(("done", result))
            except Exception as e:
                logger.error(f"Stream crawl failed: {e}", exc_info=True)
                await page_queue.put(("error", str(e)))

        # Start the crawl task
        crawl_task = asyncio.create_task(_crawl_and_enqueue())

        # Yield events as they arrive
        result_obj = None
        while True:
            try:
                # Use a timeout so we can check if the task completed
                event_type, event_data = await asyncio.wait_for(page_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if crawl_task.done():
                    # Check if queue is empty and task is done
                    if page_queue.empty():
                        break
                    continue

            if event_type == "document":
                page_dict = event_data.model_dump(exclude_none=True)
                yield sse_event("document", {"type": "document", "data": page_dict})
            elif event_type == "done":
                result_obj = event_data
                break
            elif event_type == "error":
                yield sse_event("done", {"type": "done", "data": {"success": False, "status": "failed", "error": event_data}})
                return

        # Send final done event
        if result_obj:
            done_payload = {
                "type": "done",
                "data": {
                    "success": True,
                    "status": "completed",
                    "completed": result_obj.completed,
                    "total": result_obj.total_discovered,

                },
            }
            yield sse_event("done", done_payload)

    except Exception as e:
        logger.error(f"SSE crawl stream error: {e}", exc_info=True)
        yield sse_event("done", {"type": "done", "data": {"success": False, "status": "failed", "error": str(e)}})


async def _jsonl_stream_crawl(req: CrawlRequest):
    """NDJSON (JSON Lines) generator — yields one JSON object per line as pages complete.

    Unlike SSE, NDJSON is simpler to parse: each line is a complete JSON object.
    Clients read line-by-line and parse incrementally without event parsing.
    """
    scraper_formats = []
    if req.scrape_options:
        scraper_formats = req.scrape_options.formats

    crawler = Crawler(
        browser=_browser,
        max_depth=req.max_depth or _config.crawl.max_depth,
        max_pages=req.limit or _config.crawl.max_pages,
        concurrency=_config.crawl.concurrency,
        delay=_config.crawl.delay_between_requests,
        allow_external=req.allow_external_links,
        allow_backward=req.allow_backward_crawling,
        include_paths=req.include_paths,
        exclude_paths=req.exclude_paths,
    )

    result_ref: list = [None]

    async def _on_page(page_data):
        """Called for every page as it completes — stream it immediately."""
        line = json.dumps(page_data.model_dump(exclude_none=True))
        yield line + "\n"

    async def _crawl_and_stream():
        """Run crawl with real-time page callback."""
        try:
            page_queue: asyncio.Queue = asyncio.Queue()

            async def _relay(page_data):
                await page_queue.put(page_data)

            result = await crawler.crawl(
                start_url=req.url,
                scrape_formats=scraper_formats or [OutputFormat.MARKDOWN],
                only_main_content=req.scrape_options.only_main_content if req.scrape_options else True,
                timeout=_config.browser.navigation_timeout,
                on_page=_relay,
            )
            result_ref[0] = result
            await page_queue.put(None)  # sentinel
        except Exception as e:
            logger.error(f"JSONL crawl failed: {e}", exc_info=True)
            await page_queue.put(None)

    crawl_task = asyncio.create_task(_crawl_and_stream())

    try:
        while True:
            try:
                page_data = await asyncio.wait_for(page_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if crawl_task.done() and page_queue.empty():
                    break
                continue

            if page_data is None:
                break

            line = json.dumps(page_data.model_dump(exclude_none=True))
            yield line + "\n"

        # Final summary line
        if result_ref[0]:
            summary = {
                "type": "__done__",
                "success": True,
                "status": "completed",
                "completed": result_ref[0].completed,
                "total": result_ref[0].total_discovered,
            }
            yield json.dumps(summary) + "\n"
        else:
            yield json.dumps({"type": "__done__", "success": False, "status": "failed"}) + "\n"

    except Exception as e:
        logger.error(f"JSONL crawl stream error: {e}", exc_info=True)
        yield json.dumps({"type": "__done__", "success": False, "status": "failed", "error": str(e)}) + "\n"


async def _stream_distill(req: DistillRequest):
    """SSE generator for extract — yields progress events and a final done event."""
    extractor = Extractor(
        browser=_browser,
        llm_provider=_config.extract.llm_provider,
        llm_model=_config.extract.llm_model,
        max_retries=req.max_retries,
        mental_model=req.mental_model,
    )

    try:
        # Yield scraping progress events per URL
        total_urls = len(req.urls)
        scraped_data = []

        for i, url in enumerate(req.urls):
            # Emit scraping progress
            yield sse_event("progress", {
                "type": "progress",
                "data": {
                    "step": "scraping",
                    "url": url,
                    "current": i + 1,
                    "total": total_urls,
                },
            })

            # Scrape the URL
            try:
                page_data = await extractor.scraper.scrape(
                    url=url,
                    formats=[OutputFormat.MARKDOWN],
                    only_main_content=True,
                )
                if page_data and page_data.markdown:
                    scraped_data.append({
                        "url": url,
                        "title": page_data.metadata.get("title", "") if page_data.metadata else "",
                        "length": len(page_data.markdown),
                    })
            except Exception as e:
                logger.warning(f"Failed to scrape {url}: {e}")

        # Now run extraction with all scraped content
        yield sse_event("progress", {
            "type": "progress",
            "data": {
                "step": "extracting",
                "message": "Running LLM extraction",
                "urls_scraped": len(scraped_data),
                "total_urls": total_urls,
            },
        })

        from .templates import get_template
        template = get_template(req.template) if req.template else None
        result = await extractor.extract(
            urls=req.urls,
            prompt=req.prompt,
            schema=req.schema_,
            system_prompt=req.system_prompt,
            output_format=req.format,
            template=template,
            examples=req.examples,
        )

        # Send final done event with the result
        yield sse_event("done", {"type": "done", "data": result})

    except Exception as e:
        logger.error(f"SSE extract stream error: {e}", exc_info=True)
        yield sse_event("done", {"type": "done", "data": {"success": False, "status": "failed", "error": str(e)}})

# ─── Default app instance ────────────────────────────────────────────────────

# Create a default app instance so `uvicorn huginn.api:app` works.
# For custom config, use create_app(config) or the CLI: `huginn serve`
app = create_app()


def get_app(config: Optional[HuginnConfig] = None) -> FastAPI:
    """Get or create the FastAPI app instance."""
    global app
    if config is not None:
        app = create_app(config)
    return app
