"""
Huginn Job Store — SQLite-backed async job queue.

No Redis, no Supabase. Just SQLite + asyncio.
Stores job state, results, and metadata.
"""

import aiosqlite
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
import uuid
from typing import Any, Dict, List, Optional, TYPE_CHECKING

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,        -- "sweep" or "extract"
    status TEXT NOT NULL DEFAULT 'pending',
    request_json TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT,
    completed INTEGER DEFAULT 0,
    total INTEGER
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_expires ON jobs(expires_at);

CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    job_type TEXT NOT NULL,
    cron TEXT,
    interval_seconds INTEGER,
    request_json TEXT NOT NULL,
    webhook_url TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_run TEXT,
    next_run TEXT
);

CREATE INDEX IF NOT EXISTS idx_schedules_enabled ON schedules(enabled);
"""


class JobStore:
    """Async SQLite job store."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def init(self):
        """Initialize database connection and schema."""
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        logger.info(f"JobStore initialized: {self.db_path}")

    async def close(self):
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    async def create_job(self, job_type: str, request: dict, ttl: int = 3600) -> str:
        """Create a new job and return its ID."""
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
        await self._db.execute(
            "INSERT INTO jobs (id, type, status, request_json, created_at, updated_at, expires_at) "
            "VALUES (?, ?, 'pending', ?, ?, ?, ?)",
            (job_id, job_type, json.dumps(request), now, now, expires)
        )
        await self._db.commit()
        logger.info(f"Created {job_type} job: {job_id}")
        return job_id

    async def get_job(self, job_id: str) -> Optional[dict]:
        """Get job details by ID."""
        cursor = await self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        return dict(row)

    async def update_job(
        self,
        job_id: str,
        status: Optional[str] = None,
        job_result: Optional[Any] = None,
        error: Optional[str] = None,
        completed: Optional[int] = None,
        total: Optional[int] = None,
    ):
        """Update job state."""
        sets = ["updated_at = ?"]
        params = [datetime.now(timezone.utc).isoformat()]
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if job_result is not None:
            sets.append("result_json = ?")
            params.append(json.dumps(job_result))
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if completed is not None:
            sets.append("completed = ?")
            params.append(completed)
        if total is not None:
            sets.append("total = ?")
            params.append(total)
        params.append(job_id)
        await self._db.execute(
            f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params
        )
        await self._db.commit()

    async def list_jobs(self, status: Optional[str] = None, limit: int = 50) -> List[dict]:
        """List jobs, optionally filtered by status."""
        if status:
            cursor = await self._db.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit)
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ─── Schedules ───────────────────────────────────────────────────────────────

    async def create_schedule(self, schedule_id: str, name: str, job_type: str,
                              request: dict, ttl: int = 3600,
                              cron: Optional[str] = None,
                              interval_seconds: Optional[int] = None,
                              webhook_url: Optional[str] = None) -> str:
        """Create a new schedule and return its ID."""
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO schedules (id, name, job_type, request_json, cron, interval_seconds, "
            "webhook_url, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (schedule_id, name, job_type, json.dumps(request), cron, interval_seconds,
             webhook_url, now)
        )
        await self._db.commit()
        logger.info(f"Created schedule: {schedule_id} ({name})")
        return schedule_id

    async def get_schedule(self, schedule_id: str) -> Optional[dict]:
        """Get schedule by ID."""
        cursor = await self._db.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        result = dict(row)
        result["request_json"] = json.loads(result["request_json"]) if result.get("request_json") else None
        return result

    async def list_schedules(self, limit: int = 50) -> List[dict]:
        """List all schedules."""
        cursor = await self._db.execute(
            "SELECT * FROM schedules ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["request_json"] = json.loads(result["request_json"]) if result.get("request_json") else None
            results.append(result)
        return results

    async def update_schedule(self, schedule_id: str, **kwargs) -> None:
        """Update schedule fields."""
        if not kwargs:
            return
        sets = []
        params = []
        for key, value in kwargs.items():
            col = key
            if key == "request":
                col = "request_json"
                value = json.dumps(value) if value is not None else None
            elif key in ("last_run", "next_run") and value is not None:
                value = value.isoformat() if hasattr(value, "isoformat") else value
            elif key == "enabled":
                value = 1 if value else 0
            sets.append(f"{col} = ?")
            params.append(value)
        params.append(schedule_id)
        await self._db.execute(
            f"UPDATE schedules SET {', '.join(sets)} WHERE id = ?", params
        )
        await self._db.commit()

    async def delete_schedule(self, schedule_id: str) -> bool:
        """Delete a schedule."""
        cursor = await self._db.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def cleanup_expired(self):
        """Remove expired jobs."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            "DELETE FROM jobs WHERE expires_at < ?", (now,)
        )
        deleted = cursor.rowcount
        await self._db.commit()
        if deleted:
            logger.info(f"Cleaned up {deleted} expired jobs")
        return deleted

    async def delete_job(self, job_id: str) -> bool:
        """Delete a job by ID."""
        cursor = await self._db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        await self._db.commit()
        return cursor.rowcount > 0