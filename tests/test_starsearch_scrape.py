"""Tests for Huginn's StarSearch JSON-lines bridge."""

import json

import pytest

from huginn.models import Action, ActionType, OutputFormat, ScrapeOptions
from huginn import starsearch_scrape
from huginn.utils import scrape_options_kwargs


class FakeReader:
    def __init__(self, responses):
        self.responses = [json.dumps(response).encode() + b"\n" for response in responses]

    async def readline(self):
        return self.responses.pop(0)


class FakeWriter:
    def __init__(self):
        self.payloads = []
        self.closed = False

    def write(self, data):
        self.payloads.append(json.loads(data.decode()))

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


@pytest.mark.asyncio
async def test_daemon_status_reports_live_capacity(monkeypatch):
    reader = FakeReader([
        {"starsearch": "1.0", "compatible": True},
        {
            "ok": True,
            "sid": "",
            "result": {
                "daemon_version": "0.3.0",
                "active_sessions": 2,
                "capacity": 5,
                "available": 3,
            },
        },
    ])
    writer = FakeWriter()

    async def fake_open_connection(*args, **kwargs):
        return reader, writer

    monkeypatch.setenv("HUGINN_STARSEARCH_TCP", "127.0.0.1:7676")
    monkeypatch.setattr(starsearch_scrape.asyncio, "open_connection", fake_open_connection)
    status = await starsearch_scrape.daemon_status()

    assert status["reachable"] is True
    assert status["capacity"] == 5
    assert status["available"] == 3
    assert writer.payloads[-1]["cmd"] == "status"


@pytest.mark.asyncio
async def test_fetch_page_honors_session_options_cookies_actions_and_screenshot(monkeypatch):
    reader = FakeReader([
        {"starsearch": "1.0", "compatible": True},
        {"ok": True, "sid": "sid-1", "result": {"sid": "sid-1"}},
        {"ok": True, "sid": "sid-1", "result": {}},  # cookies
        {"ok": True, "sid": "sid-1", "result": {}},  # navigate
        {"ok": True, "sid": "sid-1", "result": {}},  # click
        {"ok": True, "sid": "sid-1", "result": {}},  # type
        {"ok": True, "sid": "sid-1", "result": {"data": "cG5n"}},
        {"ok": True, "sid": "sid-1", "result": {"html": "<main>ok</main>"}},
        {"ok": True, "sid": "sid-1", "result": {}},  # close
    ])
    writer = FakeWriter()

    async def fake_open_connection(*args, **kwargs):
        return reader, writer

    monkeypatch.setattr(starsearch_scrape.asyncio, "open_connection", fake_open_connection)

    html, screenshot = await starsearch_scrape._fetch_page(
        "127.0.0.1:7676",
        "https://example.com/private",
        timeout=2,
        cookies={"session": "secret"},
        proxy={"server": "http://proxy.local:8080"},
        locale="fr-CA",
        actions=[
            {"type": "click", "selector": "#open"},
            {"type": "type", "selector": "#query", "text": "hello"},
            {"type": "screenshot"},
        ],
        screenshot=True,
    )

    assert html == "<main>ok</main>"
    assert screenshot == "cG5n"
    commands = [payload.get("cmd") for payload in writer.payloads]
    assert commands == [
        None,
        "new_session",
        "set_cookies",
        "navigate",
        "click",
        "type",
        "screenshot",
        "get_content",
        "close_session",
    ]
    opts = writer.payloads[1]["opts"]
    assert opts["proxy"] == "http://proxy.local:8080"
    assert opts["locale"] == "fr-CA"
    assert writer.payloads[2]["cookies"][0]["domain"] == "example.com"
    assert writer.closed is True


@pytest.mark.asyncio
async def test_fetch_page_accepts_pydantic_action_enum(monkeypatch):
    reader = FakeReader([
        {"starsearch": "1.0", "compatible": True},
        {"ok": True, "sid": "sid-1", "result": {"sid": "sid-1"}},
        {"ok": True, "sid": "sid-1", "result": {}},  # navigate
        {"ok": True, "sid": "sid-1", "result": {}},  # wait_for
        {"ok": True, "sid": "sid-1", "result": {"html": "<main>ok</main>"}},
        {"ok": True, "sid": "sid-1", "result": {}},  # close
    ])
    writer = FakeWriter()

    async def fake_open_connection(*args, **kwargs):
        return reader, writer

    monkeypatch.setattr(starsearch_scrape.asyncio, "open_connection", fake_open_connection)
    action = Action(type=ActionType.WAIT_FOR_SELECTOR, selector="h1", timeout=5000)

    html, _ = await starsearch_scrape._fetch_page(
        "127.0.0.1:7676",
        "https://example.com",
        actions=[action.model_dump()],
    )

    assert html == "<main>ok</main>"
    wait_command = writer.payloads[3]
    assert wait_command["cmd"] == "wait_for"
    assert wait_command["selector"] == "h1"
    assert wait_command["timeout_s"] == 5


def test_scrape_options_kwargs_serializes_action_enum_to_json_value():
    options = ScrapeOptions(
        actions=[Action(type=ActionType.WAIT_FOR_SELECTOR, selector="main")]
    )

    payload = scrape_options_kwargs(options)

    assert payload["actions"][0]["type"] == "wait_for_selector"


@pytest.mark.asyncio
async def test_fetch_page_does_not_ignore_navigation_error(monkeypatch):
    reader = FakeReader([
        {"starsearch": "1.0", "compatible": True},
        {"ok": True, "sid": "sid-1", "result": {"sid": "sid-1"}},
        {"ok": False, "sid": "sid-1", "error": "SSRFBlocked"},
        {"ok": True, "sid": "sid-1", "result": {}},
    ])
    writer = FakeWriter()

    async def fake_open_connection(*args, **kwargs):
        return reader, writer

    monkeypatch.setattr(starsearch_scrape.asyncio, "open_connection", fake_open_connection)

    with pytest.raises(RuntimeError, match="navigate failed: SSRFBlocked"):
        await starsearch_scrape._fetch_page(
            "127.0.0.1:7676", "http://127.0.0.1/admin", timeout=1
        )
    assert writer.payloads[-1]["cmd"] == "close_session"


def test_html_conversion_applies_include_and_exclude_selectors():
    data = starsearch_scrape._html_to_scrapedata(
        """
        <html><head><title>Selectors</title></head><body><main>
          <section class="keep"><h1>Wanted</h1><div class="ad">Noise</div></section>
          <section class="drop">Unwanted</section>
        </main></body></html>
        """,
        "https://example.com",
        [OutputFormat.MARKDOWN, OutputFormat.HTML],
        True,
        include_tags=[".keep"],
        exclude_tags=[".ad"],
    )

    assert "Wanted" in data.markdown
    assert "Noise" not in data.markdown
    assert "Unwanted" not in data.markdown
    assert "class=\"keep\"" in data.html
