"""Extract endpoints — structured data extraction with templates or LLM prompts."""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import StreamingResponse

from ..config import HuginnConfig
from ..models import (
    DistillRequest,
    DistillStartResponse,
    DistillStatusResponse,
    JobStatus,
)
from ..state import get_state, limiter

logger = logging.getLogger(__name__)


async def _do_start_distill(req: DistillRequest, config: HuginnConfig):
    """Core start_distill logic — shared by /v1/distill and /v1/extract alias."""
    state = get_state()
    if not state.browser:
        raise HTTPException(status_code=503, detail="Browser not initialized")

    if req.stream:
        from ..tasks import stream_distill
        return StreamingResponse(
            stream_distill(req),
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
        "extract",
        req.model_dump(exclude_none=True),
        ttl=config.server.job_ttl,
    )

    from ..tasks import run_distill
    task = asyncio.create_task(run_distill(job_id, req))
    state.crawl_tasks[job_id] = task

    return DistillStartResponse(success=True, id=job_id)


async def _do_get_distill_status(job_id: str) -> DistillStatusResponse:
    """Core get_distill_status logic — shared by /v1/distill/{id} and /v1/extract/{id} alias."""
    state = get_state()
    if not state.job_store:
        raise HTTPException(status_code=503, detail="Job store not initialized")

    job = await state.job_store.get_job(job_id)
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


def create_extract_router(config: HuginnConfig, verify_api_key) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/distill", tags=["Extract"])
    async def start_distill(req: DistillRequest, auth=Depends(verify_api_key)):
        """Start an async extraction job. Returns job ID for polling, or SSE stream if stream=True."""
        return await _do_start_distill(req, config)

    @router.get("/v1/distill/{job_id}", response_model=DistillStatusResponse, tags=["Extract"])
    @limiter.limit("100/minute")
    async def get_distill_status(request: Request, job_id: str, auth=Depends(verify_api_key)):
        """Get extraction job status and results."""
        return await _do_get_distill_status(job_id)

    return router