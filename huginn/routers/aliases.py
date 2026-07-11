"""Firecrawl-compatible route aliases.

These routes map Firecrawl API paths (e.g. /v1/scrape, /v1/crawl) to
the corresponding Huginn-native endpoints (/v1/probe, /v1/sweep, etc.).
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..config import HuginnConfig
from ..models import (
    CrawlRequest,
    CrawlStartResponse,
    DistillRequest,
    DistillStatusResponse,
    FlockRequest,
    FlockResponse,
    JobStatus,
    MapRequest,
    MapResponse,
    ScrapeRequest,
    ScrapeResponse,
    SearchRequest,
    SearchResponse,
)
from ..state import get_state, limiter

logger = logging.getLogger(__name__)


def create_aliases_router(config: HuginnConfig, verify_api_key) -> APIRouter:
    router = APIRouter()

    # ─── Scrape alias ──────────────────────────────────────────────────────

    @router.post("/v1/scrape", response_model=ScrapeResponse, tags=["Scrape"])
    @limiter.limit("100/minute")
    async def scrape_alias(request: Request, req: ScrapeRequest, auth=Depends(verify_api_key)):
        """Firecrawl-compatible alias for /v1/probe."""
        from .scrape import _do_scrape
        return await _do_scrape(request, req, config)

    # ─── Crawl aliases ─────────────────────────────────────────────────────

    @router.post("/v1/crawl", tags=["Crawl"])
    async def crawl_alias(req: CrawlRequest, auth=Depends(verify_api_key)):
        """Firecrawl-compatible alias for /v1/sweep."""
        from .crawl import _do_start_sweep
        return await _do_start_sweep(req, config)

    @router.get("/v1/crawl/{job_id}", tags=["Crawl"])
    @limiter.limit("100/minute")
    async def crawl_status_alias(request: Request, job_id: str, auth=Depends(verify_api_key)):
        """Firecrawl-compatible alias for /v1/sweep/{job_id}."""
        from .crawl import _do_get_sweep_status
        return await _do_get_sweep_status(job_id)

    @router.delete("/v1/crawl/{job_id}", tags=["Crawl"])
    @limiter.limit("100/minute")
    async def crawl_cancel_alias(request: Request, job_id: str, auth=Depends(verify_api_key)):
        """Firecrawl-compatible alias for DELETE /v1/sweep/{job_id}."""
        from .crawl import _do_cancel_crawl
        return await _do_cancel_crawl(job_id)

    @router.post("/v1/crawl/{job_id}/cancel", tags=["Crawl"])
    async def crawl_cancel_post_alias(request: Request, job_id: str, auth=Depends(verify_api_key)):
        """Firecrawl-compatible alias — POST to cancel a crawl."""
        from .crawl import _do_cancel_crawl
        return await _do_cancel_crawl(job_id)

    # ─── Map alias ─────────────────────────────────────────────────────────

    @router.post("/v1/map", response_model=MapResponse, tags=["Map"])
    @limiter.limit("60/minute")
    async def map_alias(request: Request, req: MapRequest, auth=Depends(verify_api_key)):
        """Firecrawl-compatible alias for /v1/chart."""
        from .map import _do_chart_site
        return await _do_chart_site(req)

    # ─── Extract aliases ───────────────────────────────────────────────────

    @router.post("/v1/extract", tags=["Extract"])
    async def extract_alias(req: DistillRequest, auth=Depends(verify_api_key)):
        """Firecrawl-compatible alias for /v1/distill."""
        from .extract import _do_start_distill
        return await _do_start_distill(req, config)

    @router.get("/v1/extract/{job_id}", tags=["Extract"])
    @limiter.limit("100/minute")
    async def extract_status_alias(request: Request, job_id: str, auth=Depends(verify_api_key)):
        """Firecrawl-compatible alias for /v1/distill/{job_id}."""
        from .extract import _do_get_distill_status
        return await _do_get_distill_status(job_id)

    # ─── Search alias ──────────────────────────────────────────────────────

    @router.post("/v1/search", response_model=SearchResponse, tags=["Search"])
    @limiter.limit("30/minute")
    async def search_alias(request: Request, req: SearchRequest, auth=Depends(verify_api_key)):
        """Firecrawl-compatible alias for /v1/seek."""
        from .search import _do_search
        return await _do_search(req)

    # ─── Batch scrape alias ────────────────────────────────────────────────

    @router.post("/v1/batch/scrape", tags=["Batch"])
    @limiter.limit("10/minute")
    async def batch_scrape_alias(request: Request, req: FlockRequest, auth=Depends(verify_api_key)):
        """Start an asynchronous Firecrawl-style batch scrape job."""
        import asyncio
        from ..tasks import run_flock

        state = get_state()
        if not state.job_store:
            raise HTTPException(status_code=503, detail="Job store not initialized")
        job_id = await state.job_store.create_job(
            "flock",
            req.model_dump(mode="json", by_alias=True, exclude_none=True),
            ttl=config.server.job_ttl,
        )
        task = asyncio.create_task(run_flock(job_id, req))
        state.crawl_tasks[job_id] = task
        return {
            "success": True,
            "id": job_id,
            "url": f"/v1/batch/scrape/{job_id}",
        }

    @router.get("/v1/batch/scrape/{job_id}", tags=["Batch"])
    async def batch_scrape_status_alias(job_id: str, auth=Depends(verify_api_key)):
        """Firecrawl-compatible alias for batch scrape status — maps to job store."""
        state = get_state()
        if not state.job_store:
            raise HTTPException(status_code=503, detail="Job store not initialized")
        job = await state.job_store.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        result = json.loads(job["result_json"]) if job.get("result_json") else {}
        return {
            "success": job.get("status") != "failed",
            "id": job_id,
            "status": job.get("status"),
            "completed": job.get("completed"),
            "total": job.get("total"),
            "data": result.get("results"),
            "partial": result.get("partial", False),
            "error": job.get("error"),
        }

    @router.delete("/v1/batch/scrape/{job_id}", tags=["Batch"])
    async def cancel_batch_scrape_alias(job_id: str, auth=Depends(verify_api_key)):
        """Cancel an in-flight batch scrape."""
        state = get_state()
        task = state.crawl_tasks.pop(job_id, None)
        if task:
            task.cancel()
        if state.job_store:
            await state.job_store.update_job(job_id, status="cancelled")
        return {"success": True, "id": job_id, "status": "cancelled"}

    return router
