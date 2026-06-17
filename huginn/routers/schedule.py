"""Schedule endpoints — scheduled / recurring scraping jobs."""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from ..config import HuginnConfig
from ..models import ScheduleRequest, ScheduleResponse
from ..state import get_state

logger = logging.getLogger(__name__)


def create_schedule_router(config: HuginnConfig, verify_api_key) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/schedule", response_model=ScheduleResponse, tags=["Schedule"])
    async def create_schedule(req: ScheduleRequest, auth=Depends(verify_api_key)):
        """Create a new crawl/distill schedule."""
        state = get_state()
        if not req.cron and not req.interval_seconds:
            raise HTTPException(status_code=400, detail="Provide cron or interval_seconds")

        schedule_id = str(uuid.uuid4())

        # Register with scheduler (handles DB persistence internally)
        if req.enabled:
            schedule = await state.scheduler.add_schedule(
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

    @router.get("/v1/schedule", response_model=List[ScheduleResponse], tags=["Schedule"])
    async def list_schedules(auth=Depends(verify_api_key)):
        """List all schedules."""
        state = get_state()
        schedules = await state.scheduler.list_schedules()
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

    @router.get("/v1/schedule/{schedule_id}", response_model=ScheduleResponse, tags=["Schedule"])
    async def get_schedule(schedule_id: str, auth=Depends(verify_api_key)):
        """Get a specific schedule."""
        state = get_state()
        s = await state.scheduler.get_schedule(schedule_id)
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

    @router.delete("/v1/schedule/{schedule_id}", tags=["Schedule"])
    async def delete_schedule(schedule_id: str, auth=Depends(verify_api_key)):
        """Delete a schedule."""
        state = get_state()
        await state.scheduler.delete_schedule(schedule_id)
        return {"success": True}

    @router.post("/v1/schedule/{schedule_id}/pause", tags=["Schedule"])
    async def pause_schedule(schedule_id: str, auth=Depends(verify_api_key)):
        """Pause a schedule."""
        state = get_state()
        result = await state.scheduler.pause_schedule(schedule_id)
        return {"success": True}

    @router.post("/v1/schedule/{schedule_id}/resume", tags=["Schedule"])
    async def resume_schedule(schedule_id: str, auth=Depends(verify_api_key)):
        """Resume a schedule."""
        state = get_state()
        result = await state.scheduler.resume_schedule(schedule_id)
        return {"success": True}

    return router