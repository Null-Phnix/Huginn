"""
Regression tests for Scriptorium blackreach#3: a domain circuit breaker that
opened never recovered, tripped too easily, and could not be seen or cleared.

- Every request path gates on ``is_open()``, which used to only read the state.
  Only ``call()`` moved OPEN -> HALF_OPEN, and gated requests never reached
  ``call()``, so an open circuit stayed open until restart.
- One failed scrape counted up to three times (cb.call, Scraper.scrape's own
  except block, and the router), so two bad pages opened a threshold-5 breaker.
- 403/404 pages counted against the whole host.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from huginn.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    extract_domain,
)
from huginn.domain_rate_limiter import DomainRateLimiter
from huginn.models import ScrapeData

D = "www.example.com"


async def _open(cb: CircuitBreaker, domain: str = D) -> None:
    for _ in range(cb.failure_threshold):
        await cb.record_failure(domain)
    assert cb.get_state(domain) == CircuitState.OPEN


class TestIsOpenRecovers:
    async def test_is_open_moves_to_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        await _open(cb)
        assert cb.is_open(D)

        await asyncio.sleep(0.08)

        # The gate itself must let the probe through; nothing else will.
        assert not cb.is_open(D)
        assert cb.get_state(D) == CircuitState.HALF_OPEN

    async def test_gated_caller_recovers_end_to_end(self):
        """The exact pattern every router uses: check is_open, then call()."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        await _open(cb)

        async def ok():
            return "page"

        await asyncio.sleep(0.08)
        assert not cb.is_open(D)
        assert await cb.call(D, ok) == "page"
        assert cb.get_state(D) == CircuitState.CLOSED
        assert not cb.is_open(D)

    async def test_only_one_probe_while_half_open(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        await _open(cb)
        await asyncio.sleep(0.08)

        release = asyncio.Event()

        async def slow_probe():
            await release.wait()
            return "ok"

        probe = asyncio.create_task(cb.call(D, slow_probe))
        await asyncio.sleep(0)  # let the probe take its slot

        # Everyone else is held back while the probe is in flight.
        assert cb.is_open(D)
        with pytest.raises(CircuitOpenError):
            await cb.call(D, slow_probe)

        release.set()
        assert await probe == "ok"
        assert cb.get_state(D) == CircuitState.CLOSED

    async def test_failed_probe_reopens(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        await _open(cb)
        await asyncio.sleep(0.08)

        async def boom():
            raise RuntimeError("still down")

        with pytest.raises(RuntimeError):
            await cb.call(D, boom)
        assert cb.get_state(D) == CircuitState.OPEN
        assert cb.is_open(D)

    async def test_abandoned_probe_does_not_wedge_half_open(self):
        """A probe that never reports back (cancelled task) frees its slot after the timeout."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        await _open(cb)
        await asyncio.sleep(0.08)

        async def hang():
            await asyncio.sleep(10)

        probe = asyncio.create_task(cb.call(D, hang))
        await asyncio.sleep(0)
        assert cb.is_open(D)
        probe.cancel()
        with pytest.raises(asyncio.CancelledError):
            await probe

        await asyncio.sleep(0.08)
        assert not cb.is_open(D)

    async def test_late_failure_does_not_extend_open_window(self):
        """Failures from calls that started before the circuit opened must not push recovery out."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        await _open(cb)
        await asyncio.sleep(0.06)
        await cb.record_failure(D)  # straggler
        await asyncio.sleep(0.06)
        assert not cb.is_open(D)

    async def test_straggler_failure_does_not_reopen_half_open(self):
        """Anubis review: a request that started before the half-open window
        and fails late must not reopen the circuit. Only the probe decides."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        await _open(cb)
        await asyncio.sleep(0.08)
        assert cb.get_state(D) == CircuitState.HALF_OPEN

        await cb.record_failure(D)  # straggler, holds no probe slot
        assert cb.get_state(D) == CircuitState.HALF_OPEN
        assert not cb.is_open(D)

        async def ok():
            return "page"

        assert await cb.call(D, ok) == "page"
        assert cb.get_state(D) == CircuitState.CLOSED

    async def test_straggler_call_failing_during_probe_does_not_reopen(self):
        """Same race through call(): a call admitted while CLOSED fails after
        the circuit went half-open, while the real probe is still in flight."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        straggler_release = asyncio.Event()
        probe_release = asyncio.Event()

        async def slow_fail():
            await straggler_release.wait()
            raise RuntimeError("late timeout")

        async def slow_ok():
            await probe_release.wait()
            return "ok"

        straggler = asyncio.create_task(cb.call(D, slow_fail))
        await asyncio.sleep(0)
        await _open(cb)
        await asyncio.sleep(0.08)

        probe = asyncio.create_task(cb.call(D, slow_ok))
        await asyncio.sleep(0)
        straggler_release.set()
        with pytest.raises(RuntimeError):
            await straggler
        assert cb.get_state(D) == CircuitState.HALF_OPEN

        probe_release.set()
        assert await probe == "ok"
        assert cb.get_state(D) == CircuitState.CLOSED

    async def test_straggler_success_does_not_close_half_open(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        await _open(cb)
        await asyncio.sleep(0.08)
        assert cb.get_state(D) == CircuitState.HALF_OPEN
        await cb.record_success(D)
        assert cb.get_state(D) == CircuitState.HALF_OPEN

    async def test_abandoned_probe_reporting_late_is_ignored(self):
        """After a probe slot times out, the old probe's result is stale."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        await _open(cb)
        await asyncio.sleep(0.08)
        release = asyncio.Event()

        async def late_fail():
            await release.wait()
            raise RuntimeError("finally gave up")

        old_probe = asyncio.create_task(cb.call(D, late_fail))
        await asyncio.sleep(0)
        await asyncio.sleep(0.08)  # slot times out, a new window starts
        assert not cb.is_open(D)

        release.set()
        with pytest.raises(RuntimeError):
            await old_probe
        assert cb.get_state(D) == CircuitState.HALF_OPEN
        assert not cb.is_open(D)

    async def test_retry_after_counts_down(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30)
        assert cb.retry_after(D) == 0.0
        await _open(cb)
        assert 29 < cb.retry_after(D) <= 30
        stats = await cb.get_stats()
        assert 29 <= stats[D]["retry_after_seconds"] <= 30

    async def test_can_call_alias(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30)
        assert await cb.can_call(D)
        await _open(cb)
        assert not await cb.can_call(D)


class TestFailureIf:
    async def test_returned_failure_counts(self):
        cb = CircuitBreaker(failure_threshold=2)

        async def returns_error():
            return {"status": 503}

        for _ in range(2):
            await cb.call(D, returns_error, failure_if=lambda r: r["status"] >= 500)
        assert cb.get_state(D) == CircuitState.OPEN

    async def test_returned_success_resets(self):
        cb = CircuitBreaker(failure_threshold=2)
        await cb.record_failure(D)

        async def fine():
            return {"status": 200}

        await cb.call(D, fine, failure_if=lambda r: r["status"] >= 500)
        await cb.record_failure(D)
        assert cb.get_state(D) == CircuitState.CLOSED


class TestCountsAgainstDomain:
    @pytest.mark.parametrize(
        "status,expected",
        [(200, False), (403, False), (404, False), (410, False), (429, True), (500, True), (503, True)],
    )
    def test_status_classes(self, status, expected):
        from huginn.scraper import _counts_against_domain

        assert _counts_against_domain(ScrapeData(metadata={"status_code": status})) is expected

    def test_own_circuit_rejection_is_not_a_failure(self):
        from huginn.scraper import _counts_against_domain

        data = ScrapeData(metadata={"status_code": 503, "circuit_open": True})
        assert _counts_against_domain(data) is False

    def test_missing_result_counts(self):
        from huginn.scraper import _counts_against_domain

        assert _counts_against_domain(None) is True


def _rate_limiter():
    rl = MagicMock(spec=DomainRateLimiter)
    rl.acquire_or_wait = AsyncMock(return_value=0.0)
    rl.record_outcome = AsyncMock()
    return rl


class TestScraperCountsOnce:
    async def test_exception_counts_exactly_once(self, monkeypatch):
        from huginn.scraper import Scraper

        cb = CircuitBreaker(failure_threshold=5)
        scraper = Scraper(AsyncMock(), circuit_breaker=cb, rate_limiter=_rate_limiter())
        monkeypatch.setattr(scraper, "_scrape_impl", AsyncMock(side_effect=RuntimeError("navigation failed")))

        with pytest.raises(RuntimeError):
            await scraper.scrape("https://www.example.com/p/1")

        stats = await cb.get_stats()
        assert stats[D]["consecutive_failures"] == 1

    async def test_bot_wall_pages_do_not_open_host(self, monkeypatch):
        from huginn.scraper import Scraper

        cb = CircuitBreaker(failure_threshold=3)
        scraper = Scraper(AsyncMock(), circuit_breaker=cb, rate_limiter=_rate_limiter())
        walled = ScrapeData(metadata={"url": "x", "status_code": 403, "error": "bot wall"})
        monkeypatch.setattr(scraper, "_scrape_impl", AsyncMock(return_value=walled))

        for i in range(5):
            await scraper.scrape(f"https://www.example.com/p/{i}")
        assert cb.get_state(D) == CircuitState.CLOSED

    async def test_server_errors_still_open_host(self, monkeypatch):
        from huginn.scraper import Scraper

        cb = CircuitBreaker(failure_threshold=3)
        scraper = Scraper(AsyncMock(), circuit_breaker=cb, rate_limiter=_rate_limiter())
        down = ScrapeData(metadata={"url": "x", "status_code": 502, "error": "bad gateway"})
        monkeypatch.setattr(scraper, "_scrape_impl", AsyncMock(return_value=down))

        for i in range(3):
            await scraper.scrape(f"https://www.example.com/p/{i}")
        assert cb.get_state(D) == CircuitState.OPEN


def test_extract_domain_is_case_insensitive():
    assert extract_domain("https://WWW.Example.COM/a") == "www.example.com"


class TestCircuitEndpoints:
    @pytest.fixture
    async def client(self, monkeypatch):
        import huginn.circuit_breaker as cb_mod
        from huginn.api import create_app
        from huginn.config import HuginnConfig

        monkeypatch.setattr(cb_mod, "_cb_registry", {})
        config = HuginnConfig()
        config.server.api_key = "test-key"
        app = create_app(config)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c

    AUTH = {"Authorization": "Bearer test-key"}

    async def test_health_shows_counts_but_not_hosts(self, client):
        from huginn.circuit_breaker import get_circuit_breaker

        await _open(get_circuit_breaker(), "secret-host.example")
        body = (await client.get("/health")).json()
        assert body["domain_circuits"] == {"tracked": 1, "open": 1, "half_open": 0}
        assert "secret-host.example" not in str(body)

    async def test_list_requires_auth(self, client):
        assert (await client.get("/v1/circuit-breakers")).status_code in (401, 403)

    async def test_list_and_reset_one(self, client):
        from huginn.circuit_breaker import get_circuit_breaker

        cb = get_circuit_breaker()
        await _open(cb, D)
        await _open(cb, "other.example")

        listed = (await client.get("/v1/circuit-breakers", headers=self.AUTH)).json()
        assert [entry["domain"] for entry in listed["open"]] == ["other.example", D]
        assert listed["open"][0]["retry_after_seconds"] > 0

        resp = await client.post("/v1/circuit-breakers/reset", json={"domain": "WWW.Example.com"}, headers=self.AUTH)
        assert resp.status_code == 200
        assert resp.json()["reset"] == [D]
        assert not cb.is_open(D)
        assert cb.is_open("other.example")

    async def test_reset_all(self, client):
        from huginn.circuit_breaker import get_circuit_breaker

        cb = get_circuit_breaker()
        await _open(cb, D)
        await _open(cb, "other.example")
        resp = await client.post("/v1/circuit-breakers/reset", json={}, headers=self.AUTH)
        assert sorted(resp.json()["reset"]) == ["other.example", D]
        assert not cb.is_open(D) and not cb.is_open("other.example")

    async def test_reset_requires_auth(self, client):
        assert (await client.post("/v1/circuit-breakers/reset", json={})).status_code in (401, 403)
