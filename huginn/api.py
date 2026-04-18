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
from .models import (
    Action,
    FlockRequest,
    FlockResponse,
    FlockResultItem,
    CrawlRequest,
    CrawlStartResponse,
    CrawlStatusResponse,
    ExtractRequest,
    ExtractStartResponse,
    ExtractStatusResponse,
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
    StreamExtractResponse,
)
from .job_store import JobStore
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    global _config, _browser, _job_store

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

    logger.info(f"Huginn started on {_config.server.host}:{_config.server.port}")
    logger.info(f"Browser: backend={_browser.backend}, headless={_config.browser.headless}, stealth={_config.browser.stealth_mode}")

    yield

    # Cleanup
    logger.info("Shutting down Huginn...")
    for task in _crawl_tasks.values():
        task.cancel()
    await _browser.stop()
    await _job_store.close()


def create_app(config: Optional[HuginnConfig] = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if config is None:
        config = HuginnConfig()

    app = FastAPI(
        title="Huginn",
        description="Autonomous web scraping API — Firecrawl-compatible, stealth-first, self-hosted",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.state.config = config

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

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

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "1.0.0", "browser": "running" if _browser else "stopped"}

    # ─--- Scrape ────────────────────────────────────────────────────────────

    @app.post("/v1/probe", response_model=ScrapeResponse)
    @limiter.limit("100/minute")
    async def scrape(request: Request, req: ScrapeRequest, auth=Depends(verify_api_key)):
        """Scrape a single URL and return content in requested formats."""
        if not _browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")

        scraper = Scraper(_browser)
        formats = req.formats or [OutputFormat.MARKDOWN]
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
            return ScrapeResponse(success=True, data=data)
        except Exception as e:
            logger.error(f"Scrape failed: {e}", exc_info=True)
            return ScrapeResponse(success=False, error=str(e))

    # ─--- Crawl ─────────────────────────────────────────────────────────────

    @app.post("/v1/sweep")
    async def start_sweep(req: CrawlRequest, auth=Depends(verify_api_key)):
        """Start an async crawl job. Returns job ID for polling, or SSE stream if stream=True."""
        if not _browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")

        if req.stream:
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

    @app.get("/v1/sweep/{job_id}", response_model=CrawlStatusResponse)
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
                from datetime import datetime as dt
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

    @app.delete("/v1/sweep/{job_id}")
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

    @app.post("/v1/chart", response_model=MapResponse)
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

    # ─--- Extract ───────────────────────────────────────────────────────────

    @app.post("/v1/distill")
    async def start_extract(req: ExtractRequest, auth=Depends(verify_api_key)):
        """Start an async extraction job. Returns job ID for polling, or SSE stream if stream=True."""
        if not _browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")

        if req.stream:
            return StreamingResponse(
                _stream_extract(req),
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

        task = asyncio.create_task(_run_extract(job_id, req))
        _crawl_tasks[job_id] = task

        return ExtractStartResponse(success=True, id=job_id)

    @app.get("/v1/distill/{job_id}", response_model=ExtractStatusResponse)
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

        return ExtractStatusResponse(
            success=True,
            status=status,
            data=result_data,
            error=job.get("error"),
        )

    # ─--- Search ────────────────────────────────────────────────────────────

    @app.post("/v1/seek", response_model=SearchResponse)
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
            )
            return SearchResponse(success=True, data=results)
        except Exception as e:
            logger.error(f"Search failed: {e}", exc_info=True)
            return SearchResponse(success=False, error=str(e))

    # ─--- Jobs ─────────────────────────────────────────────────────────────

    @app.get("/v1/jobs")
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

    @app.delete("/v1/jobs/{job_id}")
    @limiter.limit("100/minute")
    async def delete_job(request: Request, job_id: str, auth=Depends(verify_api_key)):
        """Delete a job."""
        if not _job_store:
            raise HTTPException(status_code=503, detail="Job store not initialized")
        deleted = await _job_store.delete_job(job_id)
        return {"success": deleted}

    # ─--- Batch Scrape ──────────────────────────────────────────────────────────

    @app.post("/v1/flock", response_model=FlockResponse)
    @limiter.limit("10/minute")
    async def flock_scrape(request: Request, req: FlockRequest, auth=Depends(verify_api_key)):
        """Scrape multiple URLs concurrently."""
        if not _browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")

        proxy_dict = build_proxy_dict(config)
        scraper = Scraper(_browser)
        sem = asyncio.Semaphore(5)
        results: List[FlockResultItem] = []

        async def scrape_one(url: str) -> FlockResultItem:
            async with sem:
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
                    return FlockResultItem(url=url, success=True, data=data)
                except Exception as e:
                    logger.error(f"Batch scrape failed for {url}: {e}")
                    return FlockResultItem(url=url, success=False, error=str(e))

        tasks = [scrape_one(url) for url in req.urls]
        items = await asyncio.gather(*tasks, return_exceptions=True)

        for item in items:
            if isinstance(item, FlockResultItem):
                results.append(item)
            elif isinstance(item, Exception):
                results.append(FlockResultItem(url="unknown", success=False, error=str(item)))

        success_count = sum(1 for r in results if r.success)
        return FlockResponse(
            success=success_count > 0,
            data=results,
        )

    return app


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


async def _run_extract(job_id: str, req: ExtractRequest):
    """Background task for extract jobs."""
    extractor = Extractor(
        browser=_browser,
        llm_provider=_config.extract.llm_provider,
        llm_model=_config.extract.llm_model,
        max_retries=req.max_retries,
        mental_model=req.mental_model,
    )

    try:
        await _job_store.update_job(job_id, status="running")

        result = await extractor.extract(
            urls=req.urls,
            prompt=req.prompt,
            schema=req.schema_,
            system_prompt=req.system_prompt,
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


async def _stream_extract(req: ExtractRequest):
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

        result = await extractor.extract(
            urls=req.urls,
            prompt=req.prompt,
            schema=req.schema_,
            system_prompt=req.system_prompt,
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
