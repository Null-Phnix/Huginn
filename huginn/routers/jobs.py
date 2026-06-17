"""Jobs endpoints — job lifecycle management (list, delete)."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import Optional

from ..config import HuginnConfig
from ..state import get_state, limiter


def create_jobs_router(config: HuginnConfig, verify_api_key) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/jobs", tags=["Jobs"])
    @limiter.limit("100/minute")
    async def list_jobs(
        request: Request,
        status: Optional[str] = Query(None),
        limit: int = Query(50, le=200),
        auth=Depends(verify_api_key),
    ):
        """List all jobs, optionally filtered by status."""
        state = get_state()
        if not state.job_store:
            raise HTTPException(status_code=503, detail="Job store not initialized")
        jobs = await state.job_store.list_jobs(status=status, limit=limit)
        return {"success": True, "jobs": jobs}

    @router.delete("/v1/jobs/{job_id}", tags=["Jobs"])
    @limiter.limit("100/minute")
    async def delete_job(request: Request, job_id: str, auth=Depends(verify_api_key)):
        """Delete a job."""
        state = get_state()
        if not state.job_store:
            raise HTTPException(status_code=503, detail="Job store not initialized")
        deleted = await state.job_store.delete_job(job_id)
        return {"success": deleted}

    return router