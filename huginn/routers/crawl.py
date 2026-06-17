"""Crawl endpoints — recursive site crawling with job tracking."""

import asyncio
import json
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import StreamingResponse

from ..config import HuginnConfig
from ..models import (
    CrawlRequest,
    CrawlStartResponse,
    CrawlStatusResponse,
    JobStatus,
    OutputFormat,
    ScrapeData,
)
from ..state import get_state, limiter

logger = logging.getLogger(__name__)


async def _do_start_sweep(req: CrawlRequest, config: HuginnConfig):
    """Core start-sweep logic — shared by /v1/sweep and /v1/crawl alias."""
    state = get_state()
    if not state.browser:
        raise HTTPException(status_code=503, detail="Browser not initialized")

    if req.stream:
        from ..tasks import stream_crawl, jsonl_stream_crawl
        if req.format == "jsonl":
            return StreamingResponse(
                jsonl_stream_crawl(req),
                media_type="application/x-ndjson",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        return StreamingResponse(
            stream_crawl(req),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    if not state.job_store:
        raise HTTPException(status_code=503, detail="Job store not initialized")

    job_id = await state.job_store.create_job(
        "sweep",
        req.model_dump(exclude_none=True),
        ttl=config.server.job_ttl,
    )

    from ..tasks import run_crawl
    task = asyncio.create_task(run_crawl(job_id, req))
    state.crawl_tasks[job_id] = task

    return CrawlStartResponse(success=True, id=job_id, url=f"/v1/sweep/{job_id}")


async def _do_get_sweep_status(job_id: str) -> CrawlStatusResponse:
    """Core get-sweep-status logic — shared by /v1/sweep/{id} and /v1/crawl/{id} alias."""
    state = get_state()
    if not state.job_store:
        raise HTTPException(status_code=503, detail="Job store not initialized")

    job = await state.job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    status = JobStatus(job["status"])
    result_data = None
    if status == JobStatus.COMPLETED and job.get("result_json"):
        result = json.loads(job["result_json"])
        result_data = [ScrapeData(**page) if isinstance(page, dict) else page for page in result.get("pages", [])]

    expires_at = None
    if job.get("expires_at"):
        try:
            from datetime import datetime, timezone
            expires_at = datetime.fromisoformat(job["expires_at"])
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


async def _do_cancel_crawl(job_id: str):
    """Core cancel-crawl logic — shared by /v1/sweep/{id} and /v1/crawl/{id} aliases."""
    state = get_state()
    if job_id in state.crawl_tasks:
        state.crawl_tasks[job_id].cancel()
        del state.crawl_tasks[job_id]
    if state.job_store:
        await state.job_store.update_job(job_id, status="cancelled")
    return {"success": True}


def create_crawl_router(config: HuginnConfig, verify_api_key) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/sweep", tags=["Crawl"])
    async def start_sweep(req: CrawlRequest, auth=Depends(verify_api_key)):
        """Start an async crawl job. Returns job ID for polling, or streams if requested."""
        return await _do_start_sweep(req, config)

    @router.get("/v1/sweep/{job_id}", response_model=CrawlStatusResponse, tags=["Crawl"])
    @limiter.limit("100/minute")
    async def get_sweep_status(request: Request, job_id: str, auth=Depends(verify_api_key)):
        """Get crawl job status and results."""
        return await _do_get_sweep_status(job_id)

    @router.delete("/v1/sweep/{job_id}", tags=["Crawl"])
    @limiter.limit("100/minute")
    async def cancel_crawl(request: Request, job_id: str, auth=Depends(verify_api_key)):
        """Cancel a running crawl job."""
        return await _do_cancel_crawl(job_id)

    return router