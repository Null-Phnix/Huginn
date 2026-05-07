"""Huginn Scheduler — cron-like recurring jobs.

Runs as a background asyncio task. Checks every 30 seconds for due schedules
and fires the appropriate job handlers with retry/webhook support.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from croniter import croniter

logger = logging.getLogger(__name__)

# Global registry of job-type -> handler function
_HANDLERS: Dict[str, callable] = {}


def register_handler(job_type: str, handler: callable) -> None:
    """Register a handler for a job type. Called by api.py on startup."""
    _HANDLERS[job_type] = handler


def _calculate_next_run(cron: Optional[str], interval_seconds: Optional[int]) -> datetime:
    """Compute the next run time from cron expression or interval."""
    now = datetime.now(timezone.utc)
    if cron:
        try:
            cron_iter = croniter(cron, now)
            return cron_iter.get_next(datetime)
        except Exception as e:
            logger.error(f"Invalid cron expression '{cron}': {e}")
            return now
    elif interval_seconds:
        return datetime.fromtimestamp(now.timestamp() + interval_seconds, tz=timezone.utc)
    return now


class Scheduler:
    """Background scheduler that fires recurring jobs."""

    def __init__(self, job_store: Any):
        self.job_store = job_store
        self._running = False
        self._task: Optional[asyncio.Task] = None

    # ─── Schedule CRUD ───────────────────────────────────────────────────────

    async def add_schedule(
        self,
        name: str,
        job_type: str,
        request: Dict[str, Any],
        cron: Optional[str] = None,
        interval_seconds: Optional[int] = None,
        webhook_url: Optional[str] = None,
        enabled: bool = True,
    ) -> Dict[str, Any]:
        """Create a new schedule and return it as a dict."""
        if not cron and not interval_seconds:
            raise ValueError("Either 'cron' or 'interval_seconds' is required")
        schedule_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        next_run = _calculate_next_run(cron, interval_seconds)

        await self.job_store.create_schedule(
            schedule_id=schedule_id,
            name=name,
            job_type=job_type,
            request=request,
            cron=cron,
            interval_seconds=interval_seconds,
            webhook_url=webhook_url,
        )

        schedule = {
            "id": schedule_id,
            "name": name,
            "job_type": job_type,
            "request": request,
            "cron": cron,
            "interval_seconds": interval_seconds,
            "webhook_url": webhook_url,
            "enabled": enabled,
            "created_at": now,
            "last_run": None,
            "next_run": next_run,
        }
        logger.info(f"Schedule created: {schedule_id} ({name}), next_run={next_run}")
        return schedule

    async def get_schedule(self, schedule_id: str) -> Optional[Dict[str, Any]]:
        """Get a schedule by ID."""
        row = await self.job_store.get_schedule(schedule_id)
        if not row:
            return None
        return self._row_to_schedule(row)

    async def list_schedules(self) -> List[Dict[str, Any]]:
        """List all schedules."""
        rows = await self.job_store.list_schedules()
        return [self._row_to_schedule(row) for row in rows]

    async def delete_schedule(self, schedule_id: str) -> bool:
        """Delete a schedule."""
        return await self.job_store.delete_schedule(schedule_id)

    async def pause_schedule(self, schedule_id: str) -> Optional[Dict[str, Any]]:
        """Pause a schedule — clears next_run and sets enabled=False."""
        await self.job_store.update_schedule(schedule_id, enabled=False, next_run=None)
        return await self.get_schedule(schedule_id)

    async def resume_schedule(self, schedule_id: str) -> Optional[Dict[str, Any]]:
        """Resume a paused schedule — recalculates next_run and sets enabled=True."""
        schedule = await self.job_store.get_schedule(schedule_id)
        if not schedule:
            return None
        next_run = _calculate_next_run(
            schedule.get("cron"), schedule.get("interval_seconds")
        )
        await self.job_store.update_schedule(schedule_id, enabled=True, next_run=next_run)
        return await self.get_schedule(schedule_id)

    def _row_to_schedule(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a DB row dict to a schedule dict with parsed datetimes."""
        def parse_dt(v):
            if not v:
                return None
            try:
                return datetime.fromisoformat(v) if isinstance(v, str) else v
            except (ValueError, TypeError):
                return None

        return {
            "id": row["id"],
            "name": row["name"],
            "job_type": row["job_type"],
            "request": row.get("request_json"),
            "cron": row.get("cron"),
            "interval_seconds": row.get("interval_seconds"),
            "webhook_url": row.get("webhook_url"),
            "enabled": bool(row.get("enabled", 1)),
            "created_at": parse_dt(row.get("created_at")),
            "last_run": parse_dt(row.get("last_run")),
            "next_run": parse_dt(row.get("next_run")),
        }

    # ─── Background loop ────────────────────────────────────────────────────

    def start(self, interval: int = 30) -> None:
        """Start the background scheduler loop."""
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._loop(interval))
            logger.info(f"Scheduler started (tick interval={interval}s)")

    async def stop(self) -> None:
        """Stop the background scheduler loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scheduler stopped")

    async def tick(self) -> List[str]:
        """Check for due schedules and fire them. Returns list of fired schedule IDs."""
        now = datetime.now(timezone.utc)
        fired_ids = []

        schedules = await self.job_store.list_schedules()
        for row in schedules:
            schedule = self._row_to_schedule(row)
            if not schedule["enabled"]:
                continue
            if not schedule["next_run"]:
                continue
            if schedule["next_run"] <= now:
                fired = await self._fire_schedule(schedule)
                if fired:
                    fired_ids.append(schedule["id"])
        return fired_ids

    async def _fire_schedule(self, schedule: Dict[str, Any]) -> bool:
        """Fire a single due schedule. Returns True on success."""
        schedule_id = schedule["id"]
        job_type = schedule["job_type"]
        request = schedule["request"]
        webhook_url = schedule["webhook_url"]

        logger.info(f"Firing schedule {schedule_id} ({schedule['name']})")
        now = datetime.now(timezone.utc)

        try:
            handler = _HANDLERS.get(job_type)
            if handler:
                await handler(request)
            else:
                logger.warning(f"No handler registered for job_type: {job_type}")

            # Compute next run
            next_run = _calculate_next_run(schedule.get("cron"), schedule.get("interval_seconds"))

            # Update last_run and next_run
            await self.job_store.update_schedule(
                schedule_id,
                last_run=now,
                next_run=next_run,
            )

            # Fire webhook
            if webhook_url:
                await self._fire_webhook(webhook_url, schedule, now)

            logger.info(f"Schedule {schedule_id} fired successfully, next_run={next_run}")
            return True

        except Exception as e:
            logger.error(f"Error firing schedule {schedule_id}: {e}", exc_info=True)
            # Still update last_run even on failure
            await self.job_store.update_schedule(
                schedule_id,
                last_run=now,
            )
            if webhook_url:
                await self._fire_webhook(
                    webhook_url, schedule, now, error=str(e)
                )
            return False

    async def _fire_webhook(
        self,
        url: str,
        schedule: Dict[str, Any],
        fired_at: datetime,
        error: Optional[str] = None,
    ) -> None:
        """Fire a webhook for a scheduled job."""
        try:
            import httpx
            payload = {
                "event": "schedule.fired",
                "schedule_id": schedule["id"],
                "schedule_name": schedule["name"],
                "job_type": schedule["job_type"],
                "fired_at": fired_at.isoformat(),
                "request": schedule["request"],
            }
            if error:
                payload["error"] = error

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                logger.info(f"Schedule webhook fired to {url}")
        except Exception as e:
            logger.warning(f"Schedule webhook failed to {url}: {e}")

    async def _loop(self, interval: int = 30) -> None:
        """Main scheduler loop."""
        while self._running:
            try:
                fired = await self.tick()
                if fired:
                    logger.info(f"Scheduler fired {len(fired)} schedule(s): {fired}")
            except Exception as e:
                logger.error(f"Scheduler tick error: {e}", exc_info=True)
            await asyncio.sleep(interval)
