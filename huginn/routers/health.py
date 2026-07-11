"""Health, readiness, and metrics endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Request

from .. import __version__
from ..metrics import get_per_endpoint_stats
from ..state import get_state, limiter


def create_health_router(verify_api_key) -> APIRouter:
    router = APIRouter()

    def proxy_status(*, detailed: bool = False) -> dict:
        provider = get_state().proxy_provider
        if provider is None:
            return {
                "mode": "uninitialized",
                "configured": False,
                "direct_egress": True,
            }
        status = provider.status()
        if detailed or not isinstance(status.get("endpoints"), list):
            return status
        endpoints = status.pop("endpoints")
        status["endpoint_count"] = len(endpoints)
        status["healthy_endpoints"] = sum(1 for endpoint in endpoints if endpoint["healthy"])
        return status

    @router.get("/health", tags=["Health"])
    async def health():
        """Comprehensive health check endpoint."""
        from ..starsearch_scrape import daemon_status

        state = get_state()
        starsearch = await daemon_status(timeout=0.75)
        return {
            "status": "degraded" if starsearch["configured"] and not starsearch["reachable"] else "ok",
            "version": __version__,
            "browser": "running" if state.browser else "stopped",
            "scheduler": "running" if (state.scheduler and state.scheduler._running) else "stopped",
            "starsearch": starsearch,
            "egress": proxy_status(),
        }

    @router.get("/health/detailed", tags=["Health"])
    async def health_detailed(auth=Depends(verify_api_key)):
        """Detailed health check with circuit breaker and cache statistics."""
        from ..cache import get_response_cache
        from ..circuit_breaker import get_circuit_breaker, extract_domain
        from ..starsearch_scrape import daemon_status

        state = get_state()
        cache = await get_response_cache()
        cb = get_circuit_breaker()

        return {
            "status": "ok",
            "version": __version__,
            "browser": "running" if state.browser else "stopped",
            "scheduler": "running" if (state.scheduler and state.scheduler._running) else "stopped",
            "circuit_breaker": await cb.get_stats(),
            "cache": await cache.stats(),
            "starsearch": await daemon_status(timeout=1.0),
            "egress": proxy_status(detailed=True),
        }

    @router.get("/health/ready", tags=["Health"])
    async def readiness():
        """Kubernetes readiness probe — returns 503 if not ready."""
        state = get_state()
        if not state.browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")
        from ..starsearch_scrape import daemon_status
        starsearch = await daemon_status(timeout=0.75)
        if starsearch["configured"] and not starsearch["reachable"]:
            raise HTTPException(status_code=503, detail={
                "message": "Configured StarSearch daemon is unavailable",
                "starsearch": starsearch,
            })
        egress = proxy_status()
        if egress.get("configured") and egress.get("healthy_endpoints", 1) == 0:
            raise HTTPException(status_code=503, detail={
                "message": "Configured proxy provider has no healthy endpoints",
                "egress": egress,
            })
        return {"ready": True, "starsearch": starsearch, "egress": egress}

    @router.get("/health/live", tags=["Health"])
    async def liveness():
        """Kubernetes liveness probe."""
        return {"alive": True}

    @router.get("/v1/metrics", tags=["Health"])
    async def metrics(auth=Depends(verify_api_key)):
        """Return per-endpoint metrics: call count, avg latency, success rate."""
        return get_per_endpoint_stats()

    return router
