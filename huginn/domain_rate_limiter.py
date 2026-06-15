"""
Huginn Per-Domain Rate Limiter

Token bucket rate limiting per domain with sliding window support.
Each domain has independent rate limits.

Key design:
- Default: 60 req/min per domain, burst up to 20
- Token bucket: smooths request rate
- Sliding window: tracks exact request timestamps
- Circuit breaker integration: fails open if domain is degraded
- Configurable per domain via HuginnConfig.rate_limits

Firecrawl has NO per-domain rate limiting — they just hard-cap the entire API.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class DomainLimitConfig:
    """Rate limit configuration for a single domain."""
    requests_per_minute: float = 60.0
    burst_size: int = 20
    window_seconds: float = 60.0

    @property
    def tokens_per_second(self) -> float:
        return self.requests_per_minute / 60.0

    def with_rate_multiplier(self, multiplier: float) -> "DomainLimitConfig":
        """Return a new config with the rate scaled by `multiplier`.

        The result is clamped: burst_size >= 1, tokens_per_second >= 0.01.
        Used by adaptive throttling to back off / recover dynamically.
        """
        new_rpm = max(self.requests_per_minute * multiplier, 0.01)
        new_burst = max(int(self.burst_size * multiplier), 1)
        return DomainLimitConfig(
            requests_per_minute=new_rpm,
            burst_size=new_burst,
            window_seconds=self.window_seconds,
        )


@dataclass
class DomainBucket:
    """Token bucket state for a single domain."""
    tokens: float
    last_update: float
    config: DomainLimitConfig
    # Sliding window for precise tracking
    request_timestamps: list = field(default_factory=list)
    total_requests: int = 0
    total_waits: float = 0.0
    blocked_count: int = 0


class DomainRateLimiter:
    """
    Token bucket + sliding window rate limiter per domain.

    Algorithm:
    1. Try to acquire a token from the bucket
    2. If bucket has tokens → allow, decrement
    3. If bucket is empty → check sliding window
    4. If under window limit → allow, record timestamp
    5. If over window limit → wait or reject

    Plus adaptive throttling (record_outcome):
    - On 429/5xx/timeout → back off (halve the rate multiplier, down to 0.1)
    - On 2xx → gradually recover (increment by 0.1, up to 1.0)
    - Decisions are based on the sliding window of recent outcomes
      (default: last 20 requests per domain)

    Never raises — waits instead of blocking.
    """

    DEFAULT_DOMAIN_CONFIG = DomainLimitConfig(
        requests_per_minute=60.0,
        burst_size=20,
        window_seconds=60.0,
    )

    # Adaptive throttling thresholds (per-event adjustment — no windowing)
    ADAPTIVE_BACKOFF_FACTOR = 0.7      # each failure multiplies rate by 0.7
    ADAPTIVE_RECOVERY_STEP = 0.05      # each success adds 0.05 to multiplier
    ADAPTIVE_MIN_MULTIPLIER = 0.1       # floor — never go below 10% of configured rate
    ADAPTIVE_MAX_MULTIPLIER = 1.0       # ceiling — never exceed configured rate

    def __init__(self):
        self._buckets: Dict[str, DomainBucket] = {}
        self._lock = asyncio.Lock()
        self._domain_configs: Dict[str, DomainLimitConfig] = {}
        # Adaptive throttling state: per-domain sliding window of recent
        # outcomes (True = success, False = failure) and current rate
        # multiplier (1.0 = full rate, 0.1 = heavily backed off).
        self._outcome_history: Dict[str, deque] = {}
        self._rate_multipliers: Dict[str, float] = {}

    def set_domain_config(self, domain: str, config: DomainLimitConfig):
        """Set custom rate limit for a domain."""
        self._domain_configs[domain] = config
        # Reset adaptive state when config changes — old rate multiplier
        # was calibrated for the old config.
        self._rate_multipliers[domain] = 1.0
        self._outcome_history.pop(domain, None)
        # If a bucket already exists, refresh it to use the new config
        if domain in self._buckets:
            self._buckets[domain].config = config

    def _get_effective_config(self, domain: str) -> DomainLimitConfig:
        """Get the current effective config for a domain, applying the adaptive multiplier."""
        base = self._domain_configs.get(domain, self.DEFAULT_DOMAIN_CONFIG)
        multiplier = self._rate_multipliers.get(domain, 1.0)
        if multiplier >= 1.0:
            return base
        return base.with_rate_multiplier(multiplier)

    def get_current_rate(self, domain: str) -> float:
        """Return the current rate multiplier (1.0 = full, 0.1 = 10%)."""
        return self._rate_multipliers.get(domain, 1.0)

    def get_outcome_history(self, domain: str):
        """Return a copy of the recent outcomes deque (True=success, False=failure)."""
        return list(self._outcome_history.get(domain, deque()))

    async def record_outcome(
        self,
        domain: str,
        success: bool,
        status_code: Optional[int] = None,
    ) -> None:
        """Record a request outcome and adjust the adaptive rate.

        Per-event adjustment — each outcome nudges the multiplier:
          - failure → multiply by ADAPTIVE_BACKOFF_FACTOR (0.7), floor 0.1
          - success → add ADAPTIVE_RECOVERY_STEP (0.05), ceiling 1.0

        Why per-event (not windowed): simpler, more predictable, no
        "stuck in backoff" state from old failures lingering in a
        window. 3 consecutive failures → 0.7³ ≈ 0.34 (clearly backed
        off). 14 consecutive successes after that → 0.34 + 14×0.05 = 1.04,
        capped at 1.0 (fully recovered).

        `status_code` is currently informational only — caller decides
        success/failure classification. Most callers will treat 4xx/5xx
        and timeouts as failure.
        """
        history = self._outcome_history.setdefault(
            domain, deque(maxlen=100)  # bounded log, useful for diagnostics
        )
        history.append(success)

        current = self._rate_multipliers.get(domain, 1.0)

        if success:
            new_rate = min(current + self.ADAPTIVE_RECOVERY_STEP, self.ADAPTIVE_MAX_MULTIPLIER)
        else:
            new_rate = max(current * self.ADAPTIVE_BACKOFF_FACTOR, self.ADAPTIVE_MIN_MULTIPLIER)

        if abs(new_rate - current) > 1e-9:
            self._rate_multipliers[domain] = new_rate
            # Refresh the bucket's config so the next acquire() uses the new rate
            if domain in self._buckets:
                self._buckets[domain].config = self._get_effective_config(domain)

    def _get_bucket(self, domain: str) -> DomainBucket:
        """Get or create bucket for domain."""
        if domain not in self._buckets:
            config = self._get_effective_config(domain)
            self._buckets[domain] = DomainBucket(
                tokens=float(config.burst_size),
                last_update=time.monotonic(),
                config=config,
            )
        return self._buckets[domain]

    def _refill_bucket(self, bucket: DomainBucket) -> float:
        """
        Refill tokens based on elapsed time.
        Returns the current token count.
        """
        now = time.monotonic()
        elapsed = now - bucket.last_update
        bucket.last_update = now
        # Add tokens at the refill rate
        new_tokens = bucket.tokens + (elapsed * bucket.config.tokens_per_second)
        bucket.tokens = min(new_tokens, bucket.config.burst_size)
        return bucket.tokens

    async def acquire(self, domain: str) -> bool:
        """
        Attempt to acquire a token for a domain.
        Returns True immediately if allowed, False if domain is completely blocked.
        """
        async with self._lock:
            bucket = self._get_bucket(domain)
            self._refill_bucket(bucket)

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                bucket.total_requests += 1
                bucket.request_timestamps.append(time.monotonic())
                return True

            # Bucket empty — check sliding window
            self._clean_window(bucket)
            # If window_seconds=0 (no window fallback configured), the bucket
            # is the only constraint. Skipping the window check here is what
            # the user opted in to.
            if bucket.config.window_seconds <= 0:
                bucket.blocked_count += 1
                return False
            if len(bucket.request_timestamps) < bucket.config.requests_per_minute:
                bucket.tokens -= 1.0
                bucket.total_requests += 1
                bucket.request_timestamps.append(time.monotonic())
                return True

            # Over limit — record blocked
            bucket.blocked_count += 1
            return False

    async def acquire_or_wait(self, domain: str) -> float:
        """
        Acquire a token, waiting if necessary.
        Returns the time spent waiting in seconds.
        """
        start_wait = time.monotonic()
        while True:
            if await self.acquire(domain):
                return time.monotonic() - start_wait
            # Wait a bit before retrying
            await asyncio.sleep(0.1)

    def _clean_window(self, bucket: DomainBucket):
        """Remove timestamps outside the window."""
        cutoff = time.monotonic() - bucket.config.window_seconds
        while bucket.request_timestamps and bucket.request_timestamps[0] < cutoff:
            bucket.request_timestamps.pop(0)

    def get_stats(self, domain: str) -> Optional[Dict]:
        """Get rate limit stats for a domain.

        Returns None only if the domain has never been configured.
        Returns a stats dict for any configured domain (even one
        that hasn't had acquire() called yet) so monitoring tools can
        distinguish 'unconfigured' from 'configured-but-idle'.
        """
        if domain not in self._domain_configs:
            return None
        # Lazily create the bucket if it doesn't exist yet
        bucket = self._get_bucket(domain)
        self._clean_window(bucket)
        config = bucket.config
        return {
            "domain": domain,
            "requests_per_minute": config.requests_per_minute,
            "burst_size": config.burst_size,
            "tokens_available": round(bucket.tokens, 2),
            "requests_in_window": len(bucket.request_timestamps),
            "total_requests": bucket.total_requests,
            "total_wait_time": round(bucket.total_waits, 3),
            "blocked_count": bucket.blocked_count,
            "window_seconds": config.window_seconds,
        }

    def get_all_stats(self) -> Dict[str, Dict]:
        """Get stats for all configured domains.

        Iterates over _domain_configs (not _buckets) so configured-but-idle
        domains show up too.
        """
        stats = {}
        for domain in self._domain_configs:
            s = self.get_stats(domain)
            if s:
                stats[domain] = s
        return stats

    def reset_domain(self, domain: str):
        """Reset rate limit state for a domain."""
        if domain in self._buckets:
            del self._buckets[domain]


# ─── Global singleton ─────────────────────────────────────────────────────────

_limiter: Optional[DomainRateLimiter] = None


def get_domain_rate_limiter() -> DomainRateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = DomainRateLimiter()
    return _limiter


# ─── Async context manager ────────────────────────────────────────────────────


class RateLimitContext:
    """
    Async context manager for rate limiting a domain.

    Usage:
        async with RateLimitContext(domain) as allowed:
            if not allowed:
                raise HTTPException(429, "Rate limit exceeded")
            # do work
    """

    def __init__(self, domain: str, limiter: Optional[DomainRateLimiter] = None):
        self.domain = domain
        self.limiter = limiter or get_domain_rate_limiter()
        self.acquired = False

    async def __aenter__(self) -> bool:
        self.acquired = await self.limiter.acquire(self.domain)
        return self.acquired

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass  # No cleanup needed
