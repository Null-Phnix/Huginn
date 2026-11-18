"""
Regression tests for Scriptorium blackreach#2.

- The fetch plane reported every StarSearch page as the requested URL with
  HTTP 200, so a delisted product that 302'd to a category page looked like a
  broken product page.
- An empty page surfaced as a bare "StarSearch returned no usable content".
- The browser-session get_content command dropped its selector and returned
  the whole page (2 MB on shop pages).
- The noise filter matched "ad" as a substring, so classes like "header",
  "lead" or "page-loaded" got deleted along with real content.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from huginn import starsearch_scrape
from huginn.models import OutputFormat
from huginn.starsearch_scrape import StarSearchEmptyPageError, is_noise_class
from tests.test_starsearch_scrape import FakeReader, FakeWriter, egress

REQUESTED = "https://shop.example/en/desktop-memory/170858/ripjaws.html"
LANDED = "https://shop.example/en/1022/desktop-memory"


def _session(*command_results):
    return FakeReader([
        {"starsearch": "1.0", "compatible": True},
        {"ok": True, "sid": "sid-1", "result": {"sid": "sid-1", "egress": egress()}},
        *({"ok": True, "sid": "sid-1", "result": result} for result in command_results),
        {"ok": True, "sid": "sid-1", "result": {}},  # close
    ])


@pytest.fixture
def daemon(monkeypatch):
    holder = {}

    def install(reader):
        writer = FakeWriter()
        holder["writer"] = writer

        async def fake_open_connection(*args, **kwargs):
            return reader, writer

        monkeypatch.setattr(starsearch_scrape.asyncio, "open_connection", fake_open_connection)
        monkeypatch.setenv("HUGINN_STARSEARCH_TCP", "127.0.0.1:7676")
        monkeypatch.setattr(starsearch_scrape, "EMPTY_CONTENT_RETRY_DELAY_S", 0)
        return holder

    return install


class TestNavigationMetadata:
    async def test_redirect_reports_final_url_and_status(self, daemon):
        daemon(_session(
            {"url": LANDED, "status": 200},
            {"html": "<html><title>Desktop Memory</title><main>RAM</main></html>", "url": LANDED},
        ))

        data = await starsearch_scrape.scrape(REQUESTED, formats=[OutputFormat.MARKDOWN], retries=1)

        assert data.metadata["url"] == LANDED
        assert data.metadata["source_url"] == REQUESTED
        assert data.metadata["redirected"] is True
        assert data.metadata["status_code"] == 200
        assert data.metadata["status_code_reported"] is True

    async def test_real_error_status_is_passed_through(self, daemon):
        daemon(_session(
            {"url": REQUESTED, "status": 404},
            {"html": "<main>Not found</main>"},
        ))

        data = await starsearch_scrape.scrape(REQUESTED, retries=1)

        assert data.metadata["status_code"] == 404
        assert data.metadata["redirected"] is False

    async def test_old_daemon_without_navigation_info_keeps_old_shape(self, daemon):
        daemon(_session({}, {"html": "<main>ok</main>"}))

        data = await starsearch_scrape.scrape(REQUESTED, retries=1)

        assert data.metadata["url"] == REQUESTED
        assert data.metadata["status_code"] == 200
        assert data.metadata["status_code_reported"] is False

    async def test_get_content_url_wins_over_navigate_url(self, daemon):
        """A client-side redirect after load shows up in get_content's url."""
        daemon(_session({"url": REQUESTED, "status": 200}, {"html": "<main>x</main>", "url": LANDED}))

        data = await starsearch_scrape.scrape(REQUESTED, retries=1)

        assert data.metadata["url"] == LANDED


class TestEmptyPage:
    async def test_empty_page_is_read_twice_before_giving_up(self, daemon):
        holder = daemon(_session(
            {"url": LANDED, "status": 200},
            {"html": ""},
            {"html": "<main>settled</main>"},
        ))

        data = await starsearch_scrape.scrape(REQUESTED, retries=1)

        assert "settled" in data.markdown
        commands = [payload.get("cmd") for payload in holder["writer"].payloads]
        assert commands.count("get_content") == 2

    async def test_empty_page_error_says_where_the_browser_ended_up(self, daemon):
        daemon(_session({"url": LANDED, "status": 200}, {"html": ""}, {"html": ""}))

        with pytest.raises(StarSearchEmptyPageError) as info:
            await starsearch_scrape.scrape(REQUESTED, retries=3)

        message = str(info.value)
        assert LANDED in message
        assert f"redirected from {REQUESTED}" in message
        assert "HTTP 200" in message

    async def test_empty_page_is_not_retried_with_new_sessions(self, daemon):
        holder = daemon(_session({}, {"html": ""}, {"html": ""}))

        with pytest.raises(StarSearchEmptyPageError):
            await starsearch_scrape.scrape(REQUESTED, retries=3)

        commands = [payload.get("cmd") for payload in holder["writer"].payloads]
        assert commands.count("new_session") == 1

    def test_maps_to_empty_content_error_code(self):
        from huginn.models import ErrorCode
        from huginn.utils import _map_exception_to_error_code

        err = StarSearchEmptyPageError(REQUESTED, LANDED, 200)
        assert _map_exception_to_error_code(err) == ErrorCode.EMPTY_CONTENT

    def test_circuit_open_error_maps_to_circuit_open(self):
        """The mapping key had a stray space and never matched."""
        from huginn.circuit_breaker import CircuitOpenError
        from huginn.models import ErrorCode
        from huginn.utils import _map_exception_to_error_code

        assert _map_exception_to_error_code(CircuitOpenError()) == ErrorCode.CIRCUIT_OPEN

    async def test_empty_page_does_not_count_against_the_host(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from huginn.circuit_breaker import CircuitBreaker
        from huginn.domain_rate_limiter import DomainRateLimiter
        from huginn.scraper import Scraper

        cb = CircuitBreaker(failure_threshold=2)
        rl = MagicMock(spec=DomainRateLimiter)
        rl.acquire_or_wait = AsyncMock(return_value=0.0)
        rl.record_outcome = AsyncMock()
        scraper = Scraper(AsyncMock(), circuit_breaker=cb, rate_limiter=rl)
        monkeypatch.setattr(
            scraper, "_scrape_impl", AsyncMock(side_effect=StarSearchEmptyPageError(REQUESTED, LANDED, 200))
        )

        for _ in range(4):
            with pytest.raises(StarSearchEmptyPageError):
                await scraper.scrape(REQUESTED)
        assert not cb.is_open("shop.example")


class TestNoiseClass:
    @pytest.mark.parametrize(
        "css_class",
        ["header", "page-header", "lead", "shadow-sm", "p-padding", "download-link",
         "product-grid-loaded", "breadcrumb", "readonly", "thread"],
    )
    def test_content_classes_are_kept(self, css_class):
        assert not is_noise_class(css_class)

    @pytest.mark.parametrize(
        "css_class",
        ["ad", "ads", "ad-slot", "top_ad", "sidebar", "sidebar-left", "navbar", "main-nav",
         "sidenav", "cookie-banner", "popup", "modal-dialog", "Sponsored"],
    )
    def test_chrome_classes_are_dropped(self, css_class):
        assert is_noise_class(css_class)

    def test_lead_paragraph_survives_conversion(self):
        html = (
            "<html><body><main><p class='lead'>Ripjaws V 64GB kit</p>"
            "<div class='ad-slot'>Buy now</div></main></body></html>"
        )
        data = starsearch_scrape._html_to_scrapedata(html, REQUESTED, [OutputFormat.MARKDOWN], True)
        assert "Ripjaws V 64GB kit" in data.markdown
        assert "Buy now" not in data.markdown


class TestSessionGetContentSelector:
    PAGE = (
        "<html><head><script type='application/ld+json'>{\"name\": \"Ripjaws\"}</script></head>"
        "<body><div class='product'>A</div><div class='product'>B</div>" + "x" * 5000 + "</body></html>"
    )

    @pytest.fixture
    def session(self, monkeypatch):
        from huginn.state import get_state

        sent = []
        replies = []

        async def command(payload, timeout=30.0):
            sent.append(payload)
            return replies.pop(0)

        monkeypatch.setattr("huginn.routers.browser_sessions.daemon_command", command)
        state = get_state()
        state.browser_sessions.clear()
        state.browser_sessions["sid-1"] = {
            "created_at": 1_700_000_000.0,
            "last_active_at": 1_700_000_000.0,
            "expires_at": 4_700_000_000.0,
            "max_idle_s": 120,
            "locale": "en-US",
            "human_level": 1,
            "allowed_domains": [],
            "allow_internal_network": False,
            "proxy_configured": False,
            "egress": egress(),
        }
        yield sent, replies
        state.browser_sessions.clear()

    async def _get_content(self, json):
        from huginn.api import create_app
        from huginn.config import HuginnConfig

        app = create_app(HuginnConfig())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/v1/browser/sessions/sid-1/commands", json=json)

    async def test_selector_is_forwarded(self, session):
        sent, replies = session
        replies.append({"ok": True, "result": {"html": "<div>A</div>", "text": "A", "matches": 1}})

        resp = await self._get_content({"command": "get_content", "selector": ".product"})

        assert resp.status_code == 200
        assert sent[-1]["selector"] == ".product"
        assert resp.json()["result"]["matches"] == 1

    async def test_old_daemon_full_page_is_narrowed_by_huginn(self, session):
        _, replies = session
        replies.append({"ok": True, "result": {"html": self.PAGE, "text": "..."}})

        resp = await self._get_content(
            {"command": "get_content", "selector": "script[type=\"application/ld+json\"]"}
        )

        result = resp.json()["result"]
        assert result["matches"] == 1
        assert result["filtered_by"] == "huginn"
        assert "Ripjaws" in result["html"]
        assert "xxxx" not in result["html"]

    async def test_no_selector_returns_whole_page(self, session):
        sent, replies = session
        replies.append({"ok": True, "result": {"html": self.PAGE, "text": "..."}})

        resp = await self._get_content({"command": "get_content"})

        assert "selector" not in sent[-1]
        assert resp.json()["result"]["html"] == self.PAGE

    @pytest.mark.parametrize("selector", ["", "   "])
    async def test_empty_selector_is_a_422_not_the_whole_page(self, session, selector):
        """Anubis review: "" was falsy, skipped, and returned the whole page."""
        sent, replies = session
        replies.append({"ok": True, "result": {"html": self.PAGE, "text": "..."}})

        resp = await self._get_content({"command": "get_content", "selector": selector})

        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "invalid_selector"
        assert not any(p.get("cmd") == "get_content" for p in sent)

    async def test_invalid_selector_is_a_422(self, session):
        _, replies = session
        replies.append({"ok": True, "result": {"html": self.PAGE, "text": "..."}})

        resp = await self._get_content({"command": "get_content", "selector": "div[[["})

        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "invalid_selector"

