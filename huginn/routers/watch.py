"""Page watch / change detection endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..config import HuginnConfig
from ..models import WatchRequest, WatchResponse, WatchStatusResponse, WatchSnapshot
from ..state import get_state
from ..utils import _map_exception_to_error_code

logger = logging.getLogger(__name__)


def create_watch_router(config: HuginnConfig, verify_api_key) -> APIRouter:
    router = APIRouter()

    from ..watcher import PageWatcher, get_watch_store, compute_content_hash
    from ..models import ErrorCode as EC

    @router.post("/v1/watch", response_model=WatchResponse, tags=["Watch"])
    async def watch_page(req: WatchRequest, auth=Depends(verify_api_key)):
        """
        Start watching a page for content changes.

        Takes an initial snapshot and returns the content hash.
        Use GET /v1/watch/{url} to check status,
        and DELETE /v1/watch/{url} to stop watching.
        """
        state = get_state()
        if not state.browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")

        from ..circuit_breaker import get_circuit_breaker
        from ..watcher import extract_domain

        domain = extract_domain(req.url)
        cb = get_circuit_breaker()

        if cb.is_open(domain):
            return WatchResponse(
                success=False,
                url=req.url,
                domain=domain,
                content_hash="",
                error=f"Circuit breaker open for {domain}",
                error_code=EC.CIRCUIT_OPEN,
            )

        store = get_watch_store()

        try:
            # Take initial snapshot
            snapshot = await state.watcher.check(req.url)
        except Exception as e:
            await cb.record_failure(domain)
            return WatchResponse(
                success=False,
                url=req.url,
                domain=domain,
                content_hash="",
                error=str(e),
                error_code=_map_exception_to_error_code(e),
            )

        await cb.record_success(domain)

        # Register watching
        entry = await store.watch(
            url=req.url,
            selectors=req.selectors,
            webhook_url=req.webhook_url,
        )
        await store.add_snapshot(req.url, snapshot)

        # Start background monitoring if interval is set
        if req.check_interval_seconds >= 60:
            await state.watcher.start_monitoring(req.url, req.check_interval_seconds)

        return WatchResponse(
            success=True,
            url=req.url,
            domain=domain,
            content_hash=snapshot.content_hash,
            change_count=0,
            last_check=snapshot.created_at,
            message="Now watching for changes. Webhook will fire on content changes.",
        )

    @router.get("/v1/watch/{url:path}", response_model=WatchStatusResponse, tags=["Watch"])
    async def get_watch_status(url: str, auth=Depends(verify_api_key)):
        """Get the current status and history of a watched page."""
        from ..watcher import extract_domain

        store = get_watch_store()
        entry = await store.get(url)

        if not entry:
            raise HTTPException(status_code=404, detail="URL is not being watched")

        return WatchStatusResponse(
            success=True,
            url=entry.url,
            domain=entry.domain,
            enabled=entry.enabled,
            webhook_url=entry.webhook_url,
            selectors=entry.selectors,
            snapshot_count=len(entry.snapshots),
            change_count=entry.change_count,
            last_check=entry.last_check,
            last_change=entry.last_change,
            latest_hash=entry.latest_snapshot().content_hash if entry.latest_snapshot() else None,
            history=[
                WatchSnapshot(
                    content_hash=s.content_hash,
                    detected_changes=s.detected_changes,
                    created_at=s.created_at,
                )
                for s in entry.snapshots
            ],
        )

    @router.post("/v1/watch/{url:path}/check", response_model=WatchResponse, tags=["Watch"])
    async def check_watch(url: str, auth=Depends(verify_api_key)):
        """
        Manually trigger a check for a watched URL.
        Returns the new snapshot and any detected changes.
        """
        from ..circuit_breaker import get_circuit_breaker
        from ..watcher import extract_domain

        state = get_state()
        cb = get_circuit_breaker()
        domain = extract_domain(url)
        store = get_watch_store()

        entry = await store.get(url)
        if not entry:
            raise HTTPException(status_code=404, detail="URL is not being watched")

        try:
            snapshot = await state.watcher.check_and_notify(url)
        except Exception as e:
            await cb.record_failure(domain)
            return WatchResponse(
                success=False,
                url=url,
                domain=domain,
                content_hash="",
                error=str(e),
                error_code=_map_exception_to_error_code(e),
            )

        return WatchResponse(
            success=True,
            url=url,
            domain=domain,
            content_hash=snapshot.content_hash,
            change_count=entry.change_count,
            last_check=snapshot.created_at,
            last_change=entry.last_change,
            message="No changes detected" if not snapshot.detected_changes else f"Detected {len(snapshot.detected_changes)} change(s)",
        )

    @router.delete("/v1/watch/{url:path}", tags=["Watch"])
    async def unwatch_page(url: str, auth=Depends(verify_api_key)):
        """Stop watching a page."""
        state = get_state()
        store = get_watch_store()
        await state.watcher.stop_monitoring(url)
        removed = await store.unwatch(url)

        return {"success": removed, "url": url}

    @router.get("/v1/watches", tags=["Watch"])
    async def list_watches(auth=Depends(verify_api_key)):
        """List all watched URLs."""
        store = get_watch_store()
        entries = await store.list_watched()
        return {
            "success": True,
            "count": len(entries),
            "watches": [e.to_dict() for e in entries],
        }

    return router