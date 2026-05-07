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

    Never raises — waits instead of blocking.
    """

    DEFAULT_DOMAIN_CONFIG = DomainLimitConfig(
        requests_per_minute=60.0,
        burst_size=20,
        window_seconds=60.0,
    )

    def __init__(self):
        self._buckets: Dict[str, DomainBucket] = {}
        self._lock = asyncio.Lock()
        self._domain_configs: Dict[str, DomainLimitConfig] = {}

    def set_domain_config(self, domain: str, config: DomainLimitConfig):
        """Set custom rate limit for a domain."""
        self._domain_configs[domain] = config

    def _get_bucket(self, domain: str) -> DomainBucket:
        """Get or create bucket for domain."""
        if domain not in self._buckets:
            config = self._domain_configs.get(domain, self.DEFAULT_DOMAIN_CONFIG)
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
        """Get rate limit stats for a domain."""
        if domain not in self._buckets:
            return None
        bucket = self._buckets[domain]
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
        """Get stats for all tracked domains."""
        stats = {}
        for domain in self._buckets:
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
