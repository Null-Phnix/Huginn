"""Replay log endpoints — scrape audit trail query and cleanup."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import HuginnConfig
from ..state import get_state


def create_replay_router(config: HuginnConfig, verify_api_key) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/replay", tags=["Replay"])
    async def list_replay(
        url: Optional[str] = None,
        status: Optional[str] = None,
        method: Optional[str] = None,
        limit: int = Query(50, le=500),
        offset: int = Query(0, ge=0),
        auth=Depends(verify_api_key),
    ):
        """List scrape replay log entries (audit trail)."""
        state = get_state()
        if not state.replay_log:
            raise HTTPException(status_code=503, detail="Replay log not initialized")
        entries = await state.replay_log.list_replay_logs(
            url=url, status=status, method=method, limit=limit, offset=offset,
        )
        return {"success": True, "count": len(entries), "entries": entries}

    @router.get("/v1/replay/stats", tags=["Replay"])
    async def replay_stats(auth=Depends(verify_api_key)):
        """Aggregate stats for the scrape replay log."""
        state = get_state()
        if not state.replay_log:
            raise HTTPException(status_code=503, detail="Replay log not initialized")
        return {"success": True, **(await state.replay_log.stats())}

    @router.get("/v1/replay/{log_id}", tags=["Replay"])
    async def get_replay_entry(log_id: str, auth=Depends(verify_api_key)):
        """Get a single replay log entry by ID."""
        state = get_state()
        if not state.replay_log:
            raise HTTPException(status_code=503, detail="Replay log not initialized")
        entry = await state.replay_log.get_replay_log(log_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Replay log entry not found")
        return {"success": True, "entry": entry}

    @router.delete("/v1/replay/cleanup", tags=["Replay"])
    async def cleanup_replay(
        max_age_hours: int = Query(168, ge=1, le=87600),
        auth=Depends(verify_api_key),
    ):
        """Remove replay log entries older than max_age_hours (default 7 days)."""
        state = get_state()
        if not state.replay_log:
            raise HTTPException(status_code=503, detail="Replay log not initialized")
        deleted = await state.replay_log.cleanup_expired(max_age_hours)
        return {"success": True, "deleted": deleted}

    return router