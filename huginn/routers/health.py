"""Health, readiness, and metrics endpoints."""

from fastapi import APIRouter, HTTPException
from fastapi import Request

from .. import __version__
from ..metrics import get_per_endpoint_stats
from ..state import get_state, limiter


def create_health_router(verify_api_key) -> APIRouter:
    router = APIRouter()

    @router.get("/health", tags=["Health"])
    async def health():
        """Comprehensive health check endpoint."""
        state = get_state()
        return {
            "status": "ok",
            "version": __version__,
            "browser": "running" if state.browser else "stopped",
            "scheduler": "running" if (state.scheduler and state.scheduler._running) else "stopped",
        }

    @router.get("/health/detailed", tags=["Health"])
    async def health_detailed():
        """Detailed health check with circuit breaker and cache statistics."""
        from ..cache import get_response_cache
        from ..circuit_breaker import get_circuit_breaker, extract_domain

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
        }

    @router.get("/health/ready", tags=["Health"])
    async def readiness():
        """Kubernetes readiness probe — returns 503 if not ready."""
        state = get_state()
        if not state.browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")
        return {"ready": True}

    @router.get("/health/live", tags=["Health"])
    async def liveness():
        """Kubernetes liveness probe."""
        return {"alive": True}

    @router.get("/v1/metrics", tags=["Health"])
    async def metrics():
        """Return per-endpoint metrics: call count, avg latency, success rate."""
        return get_per_endpoint_stats()

    return router