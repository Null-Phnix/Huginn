"""
BlackCrawl API — Firecrawl-compatible web scraping service.

Autonomous, stealth-first, self-hosted.
Built on StarSearch + Blackreach's DOM walker + mental model.

Run:
    blackcrawl serve                    # Start with defaults
    blackcrawl serve --port 8080        # Custom port
    blackcrawl serve --config path     # Custom config
    uvicorn blackcrawl.api:app         # Direct uvicorn
"""

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .config import BlackCrawlConfig, load_config
from .models import (
    Action,
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
)
from .job_store import JobStore
from .browser import BrowserManager
from .scraper import Scraper
from .crawler import Crawler
from .mapper import Mapper
from .extractor import Extractor
from .searcher import Searcher

logger = logging.getLogger(__name__)

# ─── Global State ─────────────────────────────────────────────────────────────

_config: Optional[BlackCrawlConfig] = None
_browser: Optional[BrowserManager] = None
_job_store: Optional[JobStore] = None
_crawl_tasks: dict = {}  # job_id -> asyncio.Task


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    global _config, _browser, _job_store

    _config = app.state.config
    logging.basicConfig(level=getattr(logging, _config.log_level), format="%(asctime)s %(name)s %(levelname)s %(message)s")

    # Initialize job store
    _job_store = JobStore(_config.db_path)
    await _job_store.init()

    # Initialize browser
    _browser = BrowserManager(
        headless=_config.browser.headless,
        stealth=_config.browser.stealth_mode,
        navigation_timeout=_config.browser.navigation_timeout,
        viewport=(_config.browser.viewport_width, _config.browser.viewport_height),
        user_agent=_config.browser.user_agent,
    )
    await _browser.start()

    logger.info(f"BlackCrawl started on {_config.server.host}:{_config.server.port}")
    logger.info(f"Browser: headless={_config.browser.headless}, stealth={_config.browser.stealth_mode}")

    yield

    # Cleanup
    logger.info("Shutting down BlackCrawl...")
    for task in _crawl_tasks.values():
        task.cancel()
    await _browser.stop()
    await _job_store.close()


def create_app(config: Optional[BlackCrawlConfig] = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if config is None:
        config = BlackCrawlConfig()

    app = FastAPI(
        title="BlackCrawl",
        description="Autonomous web scraping API — Firecrawl-compatible, stealth-first, self-hosted",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.state.config = config

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ─--- Auth ─────────────────────────────────────────────────────────────

    async def verify_api_key(authorization: Optional[str] = Header(None)):
        """Optional API key verification."""
        if not config.server.api_key:
            return True
        if not authorization:
            raise HTTPException(status_code=401, detail="Missing Authorization header")
        token = authorization.replace("Bearer ", "")
        if token != config.server.api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return True

    # ─--- Health ───────────────────────────────────────────────────────────

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "1.0.0", "browser": "running" if _browser else "stopped"}

    # ─--- Scrape ────────────────────────────────────────────────────────────

    @app.post("/v1/scrape", response_model=ScrapeResponse)
    async def scrape(req: ScrapeRequest, auth=Depends(verify_api_key)):
        """Scrape a single URL and return content in requested formats."""
        if not _browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")

        scraper = Scraper(_browser)
        formats = req.formats or [OutputFormat.MARKDOWN]

        try:
            data = await scraper.scrape(
                url=req.url,
                formats=formats,
                headers=req.headers,
                wait_for=req.wait_for,
                actions=[a.model_dump(by_alias=True) for a in req.actions] if req.actions else None,
                include_tags=req.include_tags,
                exclude_tags=req.exclude_tags,
                only_main_content=req.only_main_content,
                timeout=req.timeout,
            )
            return ScrapeResponse(success=True, data=data)
        except Exception as e:
            logger.error(f"Scrape failed: {e}", exc_info=True)
            return ScrapeResponse(success=False, error=str(e))

    # ─--- Crawl ─────────────────────────────────────────────────────────────

    @app.post("/v1/crawl", response_model=CrawlStartResponse)
    async def start_crawl(req: CrawlRequest, auth=Depends(verify_api_key)):
        """Start an async crawl job. Returns job ID for polling."""
        if not _browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")
        if not _job_store:
            raise HTTPException(status_code=503, detail="Job store not initialized")

        # Create job
        job_id = await _job_store.create_job(
            "crawl",
            req.model_dump(by_alias=True, exclude_none=True),
            ttl=config.server.job_ttl,
        )

        # Start background crawl
        task = asyncio.create_task(_run_crawl(job_id, req))
        _crawl_tasks[job_id] = task

        return CrawlStartResponse(success=True, id=job_id, url=f"/v1/crawl/{job_id}")

    @app.get("/v1/crawl/{job_id}", response_model=CrawlStatusResponse)
    async def get_crawl_status(job_id: str, auth=Depends(verify_api_key)):
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

        return CrawlStatusResponse(
            success=True,
            status=status,
            completed=job.get("completed", 0),
            total=job.get("total"),
            data=result_data,
            error=job.get("error"),
        )

    @app.delete("/v1/crawl/{job_id}")
    async def cancel_crawl(job_id: str, auth=Depends(verify_api_key)):
        """Cancel a running crawl job."""
        if job_id in _crawl_tasks:
            _crawl_tasks[job_id].cancel()
            del _crawl_tasks[job_id]
        if _job_store:
            await _job_store.update_job(job_id, status="cancelled")
        return {"success": True}

    # ─--- Map ──────────────────────────────────────────────────────────────

    @app.post("/v1/map", response_model=MapResponse)
    async def map_site(req: MapRequest, auth=Depends(verify_api_key)):
        """Fast URL discovery — returns all links without full content extraction."""
        if not _browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")

        mapper = Mapper(_browser)
        try:
            links = await mapper.map_site(
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

    @app.post("/v1/extract", response_model=ExtractStartResponse)
    async def start_extract(req: ExtractRequest, auth=Depends(verify_api_key)):
        """Start an async extraction job. Returns job ID for polling."""
        if not _browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")
        if not _job_store:
            raise HTTPException(status_code=503, detail="Job store not initialized")

        job_id = await _job_store.create_job(
            "extract",
            req.model_dump(by_alias=True, exclude_none=True),
            ttl=config.server.job_ttl,
        )

        task = asyncio.create_task(_run_extract(job_id, req))
        _crawl_tasks[job_id] = task

        return ExtractStartResponse(success=True, id=job_id)

    @app.get("/v1/extract/{job_id}", response_model=ExtractStatusResponse)
    async def get_extract_status(job_id: str, auth=Depends(verify_api_key)):
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

    @app.post("/v1/search", response_model=SearchResponse)
    async def search(req: SearchRequest, auth=Depends(verify_api_key)):
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
    async def list_jobs(
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
    async def delete_job(job_id: str, auth=Depends(verify_api_key)):
        """Delete a job."""
        if not _job_store:
            raise HTTPException(status_code=503, detail="Job store not initialized")
        deleted = await _job_store.delete_job(job_id)
        return {"success": deleted}

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
            pages_data.append(page.model_dump(by_alias=True, exclude_none=True))

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


# ─── Default app instance ────────────────────────────────────────────────────

# Create a default app instance so `uvicorn blackcrawl.api:app` works.
# For custom config, use create_app(config) or the CLI: `blackcrawl serve`
app = create_app()


def get_app(config: Optional[BlackCrawlConfig] = None) -> FastAPI:
    """Get or create the FastAPI app instance."""
    global app
    if config is not None:
        app = create_app(config)
    return app