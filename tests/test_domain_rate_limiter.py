"""
Tests for DomainRateLimiter — token-bucket per-domain throttling.

A core layer-3 reliability primitive. Used by the scraper to avoid
hammering the same domain when scraping multiple URLs.

Algorithm: each domain has a bucket with max_tokens capacity that
refills at tokens_per_second rate. acquire() returns True if a token
is available, False otherwise. acquire_or_wait() blocks until a token
becomes available (returns how long it waited).

These tests cover the algorithm correctness — no real network calls.
"""

import asyncio
import pytest
import time

from huginn.domain_rate_limiter import (
    DomainLimitConfig,
    DomainBucket,
    DomainRateLimiter,
    RateLimitContext,
    get_domain_rate_limiter,
)


# ─── DomainLimitConfig ──────────────────────────────────────────────────────

class TestDomainLimitConfig:
    """DomainLimitConfig computes tokens_per_second correctly."""

    def test_tokens_per_second_default(self):
        """Default 1 request per second."""
        c = DomainLimitConfig()
        assert c.tokens_per_second == 1.0

    def test_tokens_per_second_from_rpm(self):
        """requests_per_minute=60 → 1 token per second."""
        c = DomainLimitConfig(requests_per_minute=60)
        assert c.tokens_per_second == 1.0

    def test_tokens_per_second_from_rpm_120(self):
        """requests_per_minute=120 → 2 tokens per second."""
        c = DomainLimitConfig(requests_per_minute=120)
        assert c.tokens_per_second == 2.0

    def test_tokens_per_second_burst_size(self):
        """burst_size lets you exceed the rate briefly."""
        c = DomainLimitConfig(requests_per_minute=60, burst_size=5)
        assert c.burst_size == 5


# ─── DomainRateLimiter acquire ──────────────────────────────────────────────

class TestDomainRateLimiterAcquire:
    """DomainRateLimiter.acquire() — non-blocking token-bucket check."""

    def test_default_config_sensible(self):
        """Default config exists and is well-formed."""
        c = DomainLimitConfig()
        # Default requests_per_minute is 60 (token bucket base rate of 1/sec)
        assert c.requests_per_minute >= 1
        # Default burst_size is 20 (allows brief spikes)
        assert c.burst_size >= 1

    @pytest.mark.asyncio
    async def test_burst_size_allows_n_consecutive(self):
        """burst_size=3 with requests_per_minute=3 → first 3 succeed, 4th fails.
        (Both burst AND window must be 3 to test burst as the limiting factor.)
        """
        limiter = DomainRateLimiter()
        limiter.set_domain_config(
            "example.com",
            DomainLimitConfig(requests_per_minute=3, burst_size=3, window_seconds=60),
        )
        results = [await limiter.acquire("example.com") for _ in range(4)]
        assert results == [True, True, True, False]

    @pytest.mark.asyncio
    async def test_different_domains_have_independent_buckets(self):
        """a.com and b.com have separate token buckets — using a.com doesn't drain b.com."""
        limiter = DomainRateLimiter()
        # Use small burst + small window so the test is deterministic
        limiter.set_domain_config(
            "a.com",
            DomainLimitConfig(requests_per_minute=1, burst_size=1, window_seconds=60),
        )
        limiter.set_domain_config(
            "b.com",
            DomainLimitConfig(requests_per_minute=1, burst_size=1, window_seconds=60),
        )
        # Drain a.com (burst 1, window 1 → only 1 allowed)
        assert await limiter.acquire("a.com") is True
        assert await limiter.acquire("a.com") is False
        # b.com is still full
        assert await limiter.acquire("b.com") is True

    @pytest.mark.asyncio
    async def test_burst_refills_over_time(self):
        """After waiting for refill, the bucket has tokens again."""
        limiter = DomainRateLimiter()
        # 60 rpm = 1 token/sec, burst 1
        limiter.set_domain_config(
            "example.com",
            DomainLimitConfig(requests_per_minute=60, burst_size=1, window_seconds=60),
        )
        # First call: token available
        assert await limiter.acquire("example.com") is True
        # Wait 1.1 seconds — should refill 1 token
        await asyncio.sleep(1.1)
        # Token is back
        assert await limiter.acquire("example.com") is True

    @pytest.mark.asyncio
    async def test_no_refill_immediately_after_drain(self):
        """With small window, draining → immediate retry returns False."""
        limiter = DomainRateLimiter()
        # 1 rpm, burst 1, window 60 → only 1 call per minute allowed
        limiter.set_domain_config(
            "example.com",
            DomainLimitConfig(requests_per_minute=1, burst_size=1, window_seconds=60),
        )
        assert await limiter.acquire("example.com") is True
        # Immediate retry: bucket empty AND window full → blocked
        assert await limiter.acquire("example.com") is False


# ─── DomainRateLimiter get_stats / reset ─────────────────────────────────────

class TestDomainRateLimiterStats:
    """get_stats / reset_domain / get_all_stats."""

    def test_get_stats_unconfigured_domain(self):
        """get_stats for a domain without explicit config returns None."""
        limiter = DomainRateLimiter()
        assert limiter.get_stats("never-seen.com") is None

    def test_get_stats_returns_tokens_remaining(self):
        """get_stats returns a dict with current tokens and config."""
        limiter = DomainRateLimiter()
        limiter.set_domain_config(
            "example.com",
            DomainLimitConfig(requests_per_minute=60, burst_size=5),
        )
        stats = limiter.get_stats("example.com")
        assert stats is not None
        assert stats["domain"] == "example.com"
        assert "tokens_available" in stats
        # Full bucket at start
        assert stats["tokens_available"] == 5.0

    @pytest.mark.asyncio
    async def test_reset_domain_restores_full_bucket(self):
        """reset_domain() restores the bucket to burst_size."""
        limiter = DomainRateLimiter()
        # Use small burst + small window so reset → "first acquire returns True"
        limiter.set_domain_config(
            "example.com",
            DomainLimitConfig(requests_per_minute=1, burst_size=1, window_seconds=60),
        )
        # Drain the bucket (1 token + 1 window slot)
        assert await limiter.acquire("example.com") is True
        # No more tokens
        assert await limiter.acquire("example.com") is False
        # Reset
        limiter.reset_domain("example.com")
        # Bucket is full again
        assert await limiter.acquire("example.com") is True

    def test_get_all_stats_returns_configured_domains(self):
        """get_all_stats returns a dict keyed by domain with config only (not bucket state)."""
        limiter = DomainRateLimiter()
        limiter.set_domain_config("a.com", DomainLimitConfig(requests_per_minute=60))
        limiter.set_domain_config("b.com", DomainLimitConfig(requests_per_minute=120))
        all_stats = limiter.get_all_stats()
        assert set(all_stats.keys()) == {"a.com", "b.com"}
        assert all_stats["a.com"]["requests_per_minute"] == 60
        assert all_stats["b.com"]["requests_per_minute"] == 120


# ─── RateLimitContext (async context manager) ───────────────────────────────

class TestRateLimitContext:
    """RateLimitContext is an async context manager for the rate limiter."""

    def test_rate_limit_context_exists(self):
        """RateLimitContext class is importable."""
        assert RateLimitContext is not None

    @pytest.mark.asyncio
    async def test_rate_limit_context_acquires_and_releases(self):
        """RateLimitContext.__aenter__ acquires a token; __aexit__ is a no-op."""
        limiter = DomainRateLimiter()
        # Small burst + small window so the second call is blocked
        limiter.set_domain_config(
            "example.com",
            DomainLimitConfig(requests_per_minute=1, burst_size=1, window_seconds=60),
        )
        async with RateLimitContext("example.com", limiter):
            pass  # Acquired a token, released (no-op)
        # Bucket was drained and not refilled
        assert await limiter.acquire("example.com") is False

    @pytest.mark.asyncio
    async def test_rate_limit_context_uses_default_limiter(self):
        """RateLimitContext with no limiter arg uses the global default."""
        # Reset the global limiter to a known state
        from huginn.domain_rate_limiter import get_domain_rate_limiter
        get_domain_rate_limiter().reset_domain("default-test.com")
        get_domain_rate_limiter().set_domain_config(
            "default-test.com",
            DomainLimitConfig(requests_per_minute=1, burst_size=1, window_seconds=60),
        )
        # Use RateLimitContext without passing limiter — should default
        ctx = RateLimitContext("default-test.com")
        async with ctx:
            pass
        # Token was consumed from the default limiter (1 call allowed, retry blocked)
        assert await get_domain_rate_limiter().acquire("default-test.com") is False


# ─── Module-level singleton ──────────────────────────────────────────────────

class TestGetDomainRateLimiter:
    """get_domain_rate_limiter() returns a process-wide singleton."""

    def test_get_returns_instance(self):
        """get_domain_rate_limiter returns a DomainRateLimiter instance."""
        assert isinstance(get_domain_rate_limiter(), DomainRateLimiter)

    def test_get_returns_same_instance(self):
        """Subsequent calls return the same instance (singleton)."""
        a = get_domain_rate_limiter()
        b = get_domain_rate_limiter()
        assert a is b
