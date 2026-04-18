"""
BlackCrawl Job Store — SQLite-backed async job queue.

No Redis, no Supabase. Just SQLite + asyncio.
Stores job state, results, and metadata.
"""

import aiosqlite
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,        -- "crawl" or "extract"
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