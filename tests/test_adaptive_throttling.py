"""
Tests for DomainRateLimiter adaptive throttling.

Existing DomainRateLimiter is static — set_domain_config(name, cfg) sets
the rate, and that's it. Adaptive throttling observes request outcomes
and adjusts the rate dynamically:

  - 2xx response         → success → rate can grow back
  - 4xx/5xx (esp. 429)   → failure → back off
  - timeout/connection   → failure → back off

Algorithm: maintain a sliding window of recent outcomes per domain. If
the failure rate exceeds a threshold, divide the rate by 2. If the
success rate is high, gradually grow the rate back (multiplicative
recovery, capped at 1.0).

Why: a domain that starts returning 429s should be slowed down
automatically. The current static limiter is purely preventative —
this adds a reactive layer.
"""

import asyncio
import pytest
import time
from collections import deque

from huginn.domain_rate_limiter import (
    DomainLimitConfig,
    DomainRateLimiter,
)


class TestRecordOutcome:
    """record_outcome() updates the adaptive state for a domain."""

    @pytest.mark.asyncio
    async def test_record_outcome_success_increments_counter(self):
        """A success outcome is recorded in the outcome history."""
        limiter = DomainRateLimiter()
        limiter.set_domain_config("example.com", DomainLimitConfig(requests_per_minute=60))
        await limiter.record_outcome("example.com", success=True)
        history = limiter.get_outcome_history("example.com")
        assert len(history) == 1
        assert history[0] is True  # success

    @pytest.mark.asyncio
    async def test_record_outcome_failure_recorded(self):
        """A failure outcome is recorded in the outcome history."""
        limiter = DomainRateLimiter()
        limiter.set_domain_config("example.com", DomainLimitConfig(requests_per_minute=60))
        await limiter.record_outcome("example.com", success=False)
        history = limiter.get_outcome_history("example.com")
        assert history[0] is False  # failure

    @pytest.mark.asyncio
    async def test_record_outcome_429_treated_as_failure(self):
        """A 429 status code is treated as a failure (back off signal)."""
        limiter = DomainRateLimiter()
        limiter.set_domain_config("example.com", DomainLimitConfig(requests_per_minute=60))
        await limiter.record_outcome("example.com", success=False, status_code=429)
        history = limiter.get_outcome_history("example.com")
        assert history[0] is False
        # And the rate should have backed off
        assert limiter.get_current_rate("example.com") < 1.0


class TestAdaptiveBackoff:
    """After failures, the rate is automatically reduced."""

    @pytest.mark.asyncio
    async def test_single_failure_does_back_off_per_event(self):
        """Per-event algorithm: a single failure does back off (by 30%)."""
        limiter = DomainRateLimiter()
        limiter.set_domain_config("example.com", DomainLimitConfig(requests_per_minute=60))
        rate_before = limiter.get_current_rate("example.com")
        await limiter.record_outcome("example.com", success=False, status_code=429)
        rate_after = limiter.get_current_rate("example.com")
        # Per-event: each failure multiplies by 0.7
        assert rate_after < rate_before  # backed off
        assert abs(rate_after - rate_before * 0.7) < 1e-9  # exactly 30% reduction

    @pytest.mark.asyncio
    async def test_repeated_failures_back_off(self):
        """3+ consecutive failures should significantly reduce the rate."""
        limiter = DomainRateLimiter()
        limiter.set_domain_config(
            "example.com",
            DomainLimitConfig(requests_per_minute=60, burst_size=1, window_seconds=60),
        )
        rate_before = limiter.get_current_rate("example.com")
        # 3 consecutive 429s
        for _ in range(3):
            await limiter.record_outcome("example.com", success=False, status_code=429)
        rate_after = limiter.get_current_rate("example.com")
        # 0.7^3 = 0.343
        assert rate_after < rate_before * 0.4  # backed off to < 40% of original

    @pytest.mark.asyncio
    async def test_success_after_backoff_recovers_rate(self):
        """After backing off, successes gradually return the rate to normal."""
        limiter = DomainRateLimiter()
        limiter.set_domain_config(
            "example.com",
            DomainLimitConfig(requests_per_minute=60, burst_size=1, window_seconds=60),
        )
        # Back off hard
        for _ in range(5):
            await limiter.record_outcome("example.com", success=False, status_code=429)
        backed_off = limiter.get_current_rate("example.com")  # 0.7^5 = 0.168
        # Now succeed a bunch — 0.05 per success
        for _ in range(20):
            await limiter.record_outcome("example.com", success=True)
        recovered = limiter.get_current_rate("example.com")
        # Rate should be back to (or near) the original — 0.168 + 20*0.05 = 1.168, capped at 1.0
        assert recovered > backed_off
        assert recovered >= 0.99  # essentially fully recovered


class TestOutcomeHistoryWindow:
    """The outcome history is bounded — only the last N are kept."""

    @pytest.mark.asyncio
    async def test_outcome_history_capped_at_max(self):
        """History doesn't grow unbounded — only the most recent N are kept."""
        limiter = DomainRateLimiter()
        limiter.set_domain_config("example.com", DomainLimitConfig(requests_per_minute=60))
        # Record 200 outcomes (way more than the max=100 cap)
        for _ in range(200):
            await limiter.record_outcome("example.com", success=True)
        history = limiter.get_outcome_history("example.com")
        # Should be capped at 100 (the deque maxlen)
        assert len(history) == 100

    @pytest.mark.asyncio
    async def test_different_domains_have_independent_history(self):
        """outcome history is per-domain."""
        limiter = DomainRateLimiter()
        limiter.set_domain_config("a.com", DomainLimitConfig(requests_per_minute=60))
        limiter.set_domain_config("b.com", DomainLimitConfig(requests_per_minute=60))
        await limiter.record_outcome("a.com", success=False, status_code=429)
        await limiter.record_outcome("b.com", success=True)
        assert len(limiter.get_outcome_history("a.com")) == 1
        assert len(limiter.get_outcome_history("b.com")) == 1
        # And a.com backed off, b.com didn't
        assert limiter.get_current_rate("a.com") < limiter.get_current_rate("b.com")


class TestAdaptiveAcquireIntegration:
    """After backoff, acquire() uses the reduced rate."""

    @pytest.mark.asyncio
    async def test_backoff_reduces_effective_burst(self):
        """After backing off, the effective burst size is smaller — fewer rapid acquires succeed."""
        limiter = DomainRateLimiter()
        # Start with burst=20, rpm=60 (1/sec), window disabled (0)
        limiter.set_domain_config(
            "example.com",
            DomainLimitConfig(requests_per_minute=60, burst_size=20, window_seconds=0),
        )
        # Verify the base case: 20 rapid acquires all succeed
        base_results = [await limiter.acquire("example.com") for _ in range(20)]
        assert base_results.count(True) == 20

        # Now back off hard — 20 failures → multiplier = 0.7^20 ≈ 0.0008
        for _ in range(20):
            await limiter.record_outcome("example.com", success=False, status_code=429)
        # Effective config: rpm=0.048, burst=1 (clamped from 0.0016)
        # So 20 rapid acquires should mostly fail (only the initial 1 succeeds)
        backed_off_results = [await limiter.acquire("example.com") for _ in range(20)]
        assert backed_off_results.count(True) <= 2  # at most 1-2 from the clamped burst
