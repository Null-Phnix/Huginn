"""
Simple in-memory metrics tracking for Huginn API.

Tracks per-endpoint:
  - endpoint_call_count
  - endpoint_latency_ms_sum
  - endpoint_success_count
  - endpoint_failure_count

All data is in-memory only — no external dependencies, no Redis.
"""

import logging
import threading
import time
from typing import Callable, Dict, Optional

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# ─── In-memory metrics store ─────────────────────────────────────────────────

_metrics: Dict[str, Dict[str, float]] = {}
_metrics_lock = threading.Lock()


def _get_endpoint_key(scope: Scope) -> str:
    """Normalize endpoint path for metric grouping from ASGI scope."""
    path = scope.get("path", "")
    method = scope.get("method", "GET")
    # Normalize /v1/proxy/123 → /v1/proxy/{id} style placeholders
    parts = path.strip("/").split("/")
    normalized = []
    for part in parts:
        if part and _looks_like_id(part):
            normalized.append("{id}")
        else:
            normalized.append(part)
    return f"{method.upper()} /" + "/".join(normalized)


def _looks_like_id(s: str) -> bool:
    """Check if a path segment looks like an ID."""
    if len(s) < 8:
        return False
    return (
        s.count("-") >= 2
        or (s.replace("-", "").replace("_", "").isalnum() and not s.isalpha())
    )


def _get_or_create_metrics(key: str) -> Dict[str, float]:
    """Get or create metrics entry for an endpoint."""
    with _metrics_lock:
        if key not in _metrics:
            _metrics[key] = {
                "call_count": 0.0,
                "latency_ms_sum": 0.0,
                "success_count": 0.0,
                "failure_count": 0.0,
            }
        return _metrics[key]


# ─── Public API ───────────────────────────────────────────────────────────────

def record_call(method: str, endpoint: str, latency_ms: float, success: bool) -> None:
    """Record a completed request."""
    key = f"{method.upper()} {endpoint}"
    m = _get_or_create_metrics(key)
    with _metrics_lock:
        m["call_count"] += 1
        m["latency_ms_sum"] += latency_ms
        if success:
            m["success_count"] += 1
        else:
            m["failure_count"] += 1


def get_metrics() -> Dict[str, Dict[str, float]]:
    """Return a snapshot of all metrics (deep copy)."""
    with _metrics_lock:
        return {k: dict(v) for k, v in _metrics.items()}


def get_per_endpoint_stats() -> Dict[str, Dict]:
    """Return computed per-endpoint stats suitable for JSON serialization."""
    stats = {}
    with _metrics_lock:
        for key, m in _metrics.items():
            count = m["call_count"]
            if count == 0:
                avg_latency = 0.0
                success_rate = 0.0
            else:
                avg_latency = m["latency_ms_sum"] / count
                success_rate = m["success_count"] / count
            stats[key] = {
                "count": int(m["call_count"]),
                "avg_latency_ms": round(avg_latency, 3),
                "success_rate": round(success_rate, 4),
            }
    return stats


# ─── ASGI Middleware ──────────────────────────────────────────────────────────

class MetricsMiddleware:
    """
    ASGI middleware that tracks request latency and success/failure per endpoint.

    Automatically normalizes dynamic path segments (IDs) to avoid high-cardinality
    metric keys. Logs structured INFO at request completion.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        path = scope.get("path", "")
        endpoint_key = _get_endpoint_key(scope)

        start = time.perf_counter()
        success = False

        async def send_wrapper(message: dict) -> None:
            nonlocal success
            if message.get("type") == "http.response.start":
                status_code = message.get("status", 500)
                success = 200 <= status_code < 400
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            success = False
            raise
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            record_call(method, endpoint_key, elapsed_ms, success)
            logger.info(
                "request_complete",
                extra={
                    "method": method,
                    "endpoint": endpoint_key,
                    "latency_ms": round(elapsed_ms, 3),
                    "success": success,
                },
            )


# ─── Decorator (optional explicit tracking) ───────────────────────────────────

def track_request(endpoint_path: Optional[str] = None):
    """
    Decorator to explicitly track request latency and success/failure.

    Usage:
        @router.post("/v1/probe")
        @track_request("/v1/probe")
        async def scrape(...):
            ...

    Or with dynamic path resolution:
        @track_request()
        async def handler(request: Request, ...):
            ...
    """
    def decorator(fn: Callable):
        async def wrapper(request, *args, **kwargs):
            path = endpoint_path or request.url.path
            method = request.method
            start = time.perf_counter()
            success = False
            try:
                result = await fn(request, *args, **kwargs)
                success = True
                return result
            except Exception:
                success = False
                raise
            finally:
                elapsed_ms = (time.perf_counter() - start) * 1000
                record_call(method, path, elapsed_ms, success)
                logger.info(
                    "request_complete",
                    extra={
                        "method": method,
                        "endpoint": path,
                        "latency_ms": round(elapsed_ms, 3),
                        "success": success,
                    },
                )
        return wrapper
    return decorator
