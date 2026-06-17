"""
Huginn Replay Log — SQLite-backed scrape audit trail.

Records every scrape/crawl/extract call with timing, status, and
abbreviated request/response context so failures can be replayed
and debugged post-mortem. Lives in the same SQLite DB as JobStore.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS scrape_replay_log (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT 'scrape',
    status TEXT NOT NULL DEFAULT 'success',
    error TEXT,
    duration_ms INTEGER,
    request_json TEXT,
    response_summary TEXT,
    http_status INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_replay_url ON scrape_replay_log(url);
CREATE INDEX IF NOT EXISTS idx_replay_created ON scrape_replay_log(created_at);
CREATE INDEX IF NOT EXISTS idx_replay_status ON scrape_replay_log(status);
"""

_MAX_SUMMARY_LEN = 4096


def _truncate(value: str, limit: int = _MAX_SUMMARY_LEN) -> str:
    if value and len(value) > limit:
        return value[:limit] + "…"
    return value


class ReplayLog:
    """Async SQLite replay log for scrape audit trails."""

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
        logger.info(f"ReplayLog initialized: {self.db_path}")

    async def close(self):
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    async def log_scrape(
        self,
        url: str,
        method: str = "scrape",
        status: str = "success",
        error: Optional[str] = None,
        duration_ms: Optional[int] = None,
        request: Optional[Dict[str, Any]] = None,
        response_summary: Optional[Dict[str, Any]] = None,
        http_status: Optional[int] = None,
    ) -> str:
        """Record a scrape attempt and return the log entry ID."""
        log_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        request_json = json.dumps(request, default=str) if request else None
        summary_json = _truncate(json.dumps(response_summary, default=str)) if response_summary else None
        await self._db.execute(
            "INSERT INTO scrape_replay_log "
            "(id, url, method, status, error, duration_ms, request_json, "
            "response_summary, http_status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                log_id,
                url,
                method,
                status,
                _truncate(error) if error else None,
                duration_ms,
                request_json,
                summary_json,
                http_status,
                now,
            ),
        )
        await self._db.commit()
        return log_id

    async def get_replay_log(self, log_id: str) -> Optional[dict]:
        """Get a replay log entry by ID."""
        cursor = await self._db.execute(
            "SELECT * FROM scrape_replay_log WHERE id = ?", (log_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return dict(row)

    async def list_replay_logs(
        self,
        url: Optional[str] = None,
        status: Optional[str] = None,
        method: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[dict]:
        """List replay log entries, optionally filtered."""
        clauses: List[str] = []
        params: List[Any] = []
        if url:
            clauses.append("url = ?")
            params.append(url)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if method:
            clauses.append("method = ?")
            params.append(method)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        cursor = await self._db.execute(
            f"SELECT * FROM scrape_replay_log {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def cleanup_expired(self, max_age_hours: int = 168) -> int:
        """Remove replay log entries older than max_age_hours (default 7 days)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        cursor = await self._db.execute(
            "DELETE FROM scrape_replay_log WHERE created_at < ?", (cutoff,)
        )
        deleted = cursor.rowcount
        await self._db.commit()
        if deleted:
            logger.info(f"Cleaned up {deleted} expired replay log entries")
        return deleted

    async def stats(self) -> dict:
        """Return aggregate stats for the replay log."""
        cursor = await self._db.execute(
            "SELECT status, COUNT(*) as count FROM scrape_replay_log GROUP BY status"
        )
        rows = await cursor.fetchall()
        by_status = {row["status"]: row["count"] for row in rows}
        cursor = await self._db.execute(
            "SELECT method, COUNT(*) as count FROM scrape_replay_log GROUP BY method"
        )
        rows = await cursor.fetchall()
        by_method = {row["method"]: row["count"] for row in rows}
        cursor = await self._db.execute("SELECT COUNT(*) as total FROM scrape_replay_log")
        row = await cursor.fetchone()
        total = row["total"] if row else 0
        return {"total": total, "by_status": by_status, "by_method": by_method}