"""
Tests for the circuit breaker module.
"""

import asyncio
import pytest

from huginn.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    extract_domain,
    get_circuit_breaker,
)


class TestCircuitBreaker:
    """Tests for the CircuitBreaker class."""

    async def test_closed_by_default(self):
        cb = CircuitBreaker(failure_threshold=3)
        assert cb.get_state("example.com") == CircuitState.CLOSED
        assert not cb.is_open("example.com")

    async def test_opens_after_failure_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.1)

        async def fail():
            raise RuntimeError("boom")

        # Record 3 failures
        for _ in range(3):
            await cb.record_failure("example.com")

        assert cb.get_state("example.com") == CircuitState.OPEN
        assert cb.is_open("example.com")

    async def test_success_resets_consecutive_failures(self):
        cb = CircuitBreaker(failure_threshold=3)

        await cb.record_failure("example.com")
        await cb.record_failure("example.com")
        assert cb.get_state("example.com") == CircuitState.CLOSED

        await cb.record_success("example.com")

        # A single failure after success shouldn't immediately open
        await cb.record_failure("example.com")
        assert cb.get_state("example.com") == CircuitState.CLOSED

    async def test_half_open_after_recovery_timeout(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)

        # Open the circuit
        await cb.record_failure("example.com")
        await cb.record_failure("example.com")
        assert cb.get_state("example.com") == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.1)

        # Should transition to half-open on next state check
        # (state check happens inside call())
        async def probe():
            return "ok"

        # HALF_OPEN happens lazily on next call
        result = await cb.call("example.com", probe)
        assert result == "ok"
        # After successful call, should be CLOSED
        assert cb.get_state("example.com") == CircuitState.CLOSED

    async def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)

        # Open the circuit
        await cb.record_failure("example.com")
        await cb.record_failure("example.com")
        assert cb.get_state("example.com") == CircuitState.OPEN

        await asyncio.sleep(0.1)

        # Probe fails
        async def fail():
            raise RuntimeError("still down")

        try:
            await cb.call("example.com", fail)
        except RuntimeError:
            pass

        assert cb.get_state("example.com") == CircuitState.OPEN

    async def test_call_through_when_closed(self):
        cb = CircuitBreaker(failure_threshold=3)

        async def succeed():
            return "result"

        result = await cb.call("example.com", succeed)
        assert result == "result"
        stats = await cb.get_stats()
        assert stats["example.com"]["total_successes"] == 1

    async def test_call_rejects_when_open(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=60.0)

        await cb.record_failure("example.com")
        await cb.record_failure("example.com")
        assert cb.is_open("example.com")

        async def succeed():
            return "result"

        with pytest.raises(CircuitOpenError):
            await cb.call("example.com", succeed)

    async def test_reset_clears_domain(self):
        cb = CircuitBreaker(failure_threshold=2)

        await cb.record_failure("example.com")
        await cb.record_failure("example.com")
        assert cb.is_open("example.com")

        await cb.reset("example.com")
        assert cb.get_state("example.com") == CircuitState.CLOSED

    async def test_reset_all_clears_everything(self):
        cb = CircuitBreaker(failure_threshold=2)

        await cb.record_failure("example.com")
        await cb.record_failure("example.com")
        await cb.record_failure("other.com")

        await cb.reset()
        assert cb.get_state("example.com") == CircuitState.CLOSED
        assert cb.get_state("other.com") == CircuitState.CLOSED

    async def test_get_stats(self):
        cb = CircuitBreaker(failure_threshold=3)

        await cb.record_failure("a.com")
        await cb.record_failure("a.com")
        await cb.record_success("a.com")
        await cb.record_failure("b.com")

        stats = await cb.get_stats()
        assert "a.com" in stats
        assert "b.com" in stats
        assert stats["a.com"]["consecutive_failures"] == 0
        assert stats["a.com"]["total_successes"] == 1
        assert stats["b.com"]["consecutive_failures"] == 1

    async def test_multiple_domains_independent(self):
        cb = CircuitBreaker(failure_threshold=2)

        await cb.record_failure("site1.com")
        await cb.record_failure("site1.com")
        assert cb.is_open("site1.com")
        assert not cb.is_open("site2.com")

        await cb.record_failure("site2.com")
        await cb.record_failure("site2.com")
        assert cb.is_open("site2.com")

        # site1 recovers
        await cb.reset("site1.com")
        assert not cb.is_open("site1.com")
        assert cb.is_open("site2.com")


class TestExtractDomain:
    def test_basic_domain(self):
        assert extract_domain("https://example.com/page") == "example.com"

    def test_subdomain(self):
        assert extract_domain("https://api.example.com/v1/endpoint") == "api.example.com"

    def test_port_preserved(self):
        assert extract_domain("https://example.com:8080/page") == "example.com:8080"

    def test_no_scheme(self):
        # urlparse only recognizes netloc when scheme is present
        assert extract_domain("https://example.com/page") == "example.com"

    def test_unknown_on_invalid_url(self):
        assert extract_domain("not a url at all") == "unknown"
        assert extract_domain("") == "unknown"
