"""
Huginn Circuit Breaker

Prevents cascade failures when a site is down or rate-limited.
Implements the circuit breaker pattern with three states:

- CLOSED:  normal operation, requests pass through
- OPEN:    failures threshold exceeded, requests fail fast
- HALF_OPEN: testing if the site recovered, allows one probe request

Per-domain circuit breakers so one bad site doesn't affect others.
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    Per-domain circuit breaker.

    Tracks consecutive failures per domain. When the failure threshold
    is reached, the circuit opens and subsequent requests to that
    domain are rejected immediately for a cooldown period, preventing
    both resource waste and cascade overload.

    After the cooldown expires, the circuit enters HALF_OPEN state
    and allows exactly one probe request through. If the probe succeeds,
    the circuit closes. If it fails, the circuit opens again.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 1,
        monitor_window: float = 120.0,
    ):
        """
        Args:
            failure_threshold: consecutive failures before opening circuit
            recovery_timeout: seconds to wait before transitioning OPEN -> HALF_OPEN
            half_open_max_calls: number of calls allowed in HALF_OPEN state
            monitor_window: window in seconds for tracking failure rate
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.monitor_window = monitor_window

        # Per-domain state: domain -> CircuitInfo
        self._domains: dict[str, _CircuitInfo] = {}
        self._lock = asyncio.Lock()

    # ── public API ─────────────────────────────────────────────────────────────

    async def call(self, domain: str, fn, *args, failure_if=None, **kwargs):
        """
        Execute ``fn(*args, **kwargs)`` through the circuit breaker for *domain*.

        Returns the result of fn() on success. An exception counts as a
        failure. When *failure_if* is given, a returned result for which
        ``failure_if(result)`` is true also counts as a failure (scrapers
        return structured error results instead of raising).

        Raises CircuitOpenError when the circuit is open, or when it is
        half-open and its probe slots are already taken.
        """
        async with self._lock:
            info = self._domains.get(domain)
            if info is not None:
                state = self._advance(domain, info, time.monotonic())
                if state == CircuitState.OPEN:
                    raise CircuitOpenError(f"Circuit open for {domain}, site may be down")
                if state == CircuitState.HALF_OPEN:
                    if info.half_open_calls >= self.half_open_max_calls:
                        raise CircuitOpenError(f"Circuit half-open, probe already in flight for {domain}")
                    info.half_open_calls += 1

        # Allow the call
        try:
            result = await fn(*args, **kwargs)
        except Exception:
            await self._record_failure(domain)
            raise
        if failure_if is not None and failure_if(result):
            await self._record_failure(domain)
        else:
            await self._record_success(domain)
        return result

    def is_open(self, domain: str) -> bool:
        """Return True if requests to *domain* should be rejected right now.

        This is the gate every request path checks before scraping, so it
        must apply the time-based OPEN -> HALF_OPEN transition itself.
        Before it did, only ``call()`` ever moved a circuit out of OPEN, and
        gated requests never reached ``call()``: one bad burst blocked a
        host until the process restarted.

        A half-open circuit whose probe slots are all taken also counts as
        open, so concurrent callers wait for the probe instead of piling on.
        """
        info = self._domains.get(domain)
        if info is None:
            return False
        # No await between read and write, so this is safe on the event
        # loop without taking the asyncio lock.
        state = self._advance(domain, info, time.monotonic())
        if state == CircuitState.OPEN:
            return True
        if state == CircuitState.HALF_OPEN:
            return info.half_open_calls >= self.half_open_max_calls
        return False

    async def can_call(self, domain: str) -> bool:
        """Async inverse of :meth:`is_open` (used by ResearchAgent)."""
        return not self.is_open(domain)

    def retry_after(self, domain: str) -> float:
        """Seconds until an open circuit for *domain* lets a probe through (0 if not open)."""
        info = self._domains.get(domain)
        if info is None or info.state != CircuitState.OPEN:
            return 0.0
        return max(0.0, self.recovery_timeout - (time.monotonic() - info.state_since))

    def get_state(self, domain: str) -> CircuitState:
        """Return the current circuit state for domain."""
        info = self._domains.get(domain)
        if info is None:
            return CircuitState.CLOSED
        return self._advance(domain, info, time.monotonic())

    async def record_failure(self, domain: str):
        """Record a failure for domain (public API)."""
        await self._record_failure(domain)

    async def record_success(self, domain: str):
        """Record a success for domain (public API)."""
        await self._record_success(domain)

    async def get_stats(self) -> dict[str, dict]:
        """Return circuit breaker stats for all tracked domains."""
        async with self._lock:
            now = time.monotonic()
            for domain, info in self._domains.items():
                self._advance(domain, info, now)
            return {
                domain: {
                    "state": info.state.value,
                    "consecutive_failures": info.consecutive_failures,
                    "total_failures": info.total_failures,
                    "total_successes": info.total_successes,
                    "last_failure_time": info.last_failure_time,
                    "last_success_time": info.last_success_time,
                    "failure_rate_5m": info.recent_failure_rate(),
                    "retry_after_seconds": round(self.retry_after(domain), 1),
                }
                for domain, info in self._domains.items()
            }

    async def reset(self, domain: Optional[str] = None):
        """
        Reset circuit for *domain*, or all domains if domain is None.
        Returns circuit to CLOSED state with zero failures.
        """
        async with self._lock:
            if domain:
                self._domains.pop(domain, None)
            else:
                self._domains.clear()

    # ── internal ─────────────────────────────────────────────────────────────

    def _advance(self, domain: str, info: _CircuitInfo, now: float) -> CircuitState:
        """Apply time-based transitions to *info* and return its state.

        - OPEN for at least ``recovery_timeout`` -> HALF_OPEN (probe slots reset).
        - HALF_OPEN whose probes never reported back (cancelled task, crashed
          caller) for another ``recovery_timeout`` -> probe slots reset, so the
          circuit can't wedge half-open either.
        """
        if info.state == CircuitState.OPEN and now - info.state_since >= self.recovery_timeout:
            info.state = CircuitState.HALF_OPEN
            info.state_since = now
            info.half_open_calls = 0
            logger.info(f"Circuit breaker HALF_OPEN for {domain}")
        elif (
            info.state == CircuitState.HALF_OPEN
            and info.half_open_calls >= self.half_open_max_calls
            and now - info.state_since >= self.recovery_timeout
        ):
            info.state_since = now
            info.half_open_calls = 0
            logger.info(f"Circuit breaker HALF_OPEN probe timed out for {domain}, allowing a new probe")
        return info.state

    async def _record_success(self, domain: str):
        async with self._lock:
            info = self._domains.setdefault(domain, _CircuitInfo())
            info.total_successes += 1
            info.last_success_time = time.monotonic()
            info.consecutive_failures = 0

            # HALF_OPEN success -> CLOSED
            if info.state == CircuitState.HALF_OPEN:
                info.state = CircuitState.CLOSED
                info.state_since = time.monotonic()
                logger.info(f"Circuit breaker CLOSED (recovered) for {domain}")

    async def _record_failure(self, domain: str):
        async with self._lock:
            info = self._domains.setdefault(domain, _CircuitInfo())
            info.total_failures += 1
            info.last_failure_time = time.monotonic()
            info.consecutive_failures += 1

            if info.state == CircuitState.HALF_OPEN:
                # Any failure in HALF_OPEN reopens immediately
                info.state = CircuitState.OPEN
                info.state_since = time.monotonic()
                logger.warning(f"Circuit breaker re-OPEN for {domain} (half-open probe failed)")
            elif (
                info.state == CircuitState.CLOSED
                and info.consecutive_failures >= self.failure_threshold
            ):
                # Only a CLOSED circuit opens here. Late failures from calls
                # that started before the circuit opened must not push an
                # OPEN circuit's recovery window further out.
                info.state = CircuitState.OPEN
                info.state_since = time.monotonic()
                logger.warning(
                    f"Circuit breaker OPEN for {domain} "
                    f"({info.consecutive_failures} consecutive failures)"
                )


class _CircuitInfo:
    """Mutable state for a single domain's circuit."""

    __slots__ = (
        "state",
        "state_since",
        "consecutive_failures",
        "total_failures",
        "total_successes",
        "last_failure_time",
        "last_success_time",
        "half_open_calls",
        "_failure_timestamps",
    )

    def __init__(self):
        self.state = CircuitState.CLOSED
        self.state_since = time.monotonic()
        self.consecutive_failures = 0
        self.total_failures = 0
        self.total_successes = 0
        self.last_failure_time: float = 0.0
        self.last_success_time: float = 0.0
        self.half_open_calls = 0
        self._failure_timestamps: list[float] = []

    def recent_failure_rate(self) -> float:
        """Return fraction of failures in the last monitor_window seconds."""
        if not self._failure_timestamps:
            return 0.0
        now = time.monotonic()
        cutoff = now - 120.0
        recent = [t for t in self._failure_timestamps if t >= cutoff]
        self._failure_timestamps = recent
        total_calls = self.total_successes + self.total_failures
        if total_calls == 0:
            return 0.0
        return len(recent) / min(total_calls, len(recent) + self.total_successes)


class CircuitOpenError(Exception):
    """Raised when a circuit breaker is open and the call was rejected."""

    def __init__(self, message: str = "Circuit breaker is open"):
        self.message = message
        super().__init__(message)


# ── Global circuit breaker registry ──────────────────────────────────────────

_cb_registry: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(name: str = "default", **kwargs) -> CircuitBreaker:
    """
    Get or create a named circuit breaker.

    All scrapers sharing the same name share the same circuit state.
    """
    if name not in _cb_registry:
        _cb_registry[name] = CircuitBreaker(**kwargs)
    return _cb_registry[name]


def extract_domain(url: str) -> str:
    """Return the lowercased domain (netloc) from a URL, or 'unknown'."""
    try:
        return urlparse(url).netloc.lower() or "unknown"
    except Exception:
        return "unknown"
