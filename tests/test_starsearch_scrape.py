"""Tests for Huginn's StarSearch JSON-lines bridge."""

import hashlib
import json

import pytest

from huginn import starsearch_scrape
from huginn.models import Action, ActionType, OutputFormat, ScrapeOptions
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


def egress(*, proxied=False, server="http://proxy.local:8080", username=""):
    return {
        "gateway_enforced": True,
        "mode": "upstream" if proxied else "direct",
        "upstream_scheme": "http" if proxied else None,
        "upstream_identity": (
            hashlib.sha256(f"{server}\0{username}".encode()).hexdigest()
            if proxied
            else None
        ),
        "resolution": "local_frozen",
    }


def test_handshake_reads_tcp_token_file(monkeypatch, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("a" * 64)
    token_file.chmod(0o600)
    monkeypatch.setenv("HUGINN_STARSEARCH_TOKEN_FILE", str(token_file))

    payload = starsearch_scrape._handshake("test-client")

    assert payload == {
        "starsearch": "1.0",
        "client_version": "test-client",
        "auth_token": "a" * 64,
    }


def test_handshake_rejects_broad_tcp_token_permissions(monkeypatch, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("a" * 64)
    token_file.chmod(0o644)
    monkeypatch.setenv("HUGINN_STARSEARCH_TOKEN_FILE", str(token_file))

    with pytest.raises(RuntimeError, match="0600 or stricter"):
        starsearch_scrape._handshake("test-client")


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
        {"ok": True, "sid": "sid-1", "result": {"sid": "sid-1", "egress": egress(proxied=True)}},
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

    html, screenshot, descriptor = await starsearch_scrape._fetch_page(
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
    assert descriptor == egress(proxied=True)
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
    assert opts["proxy"] == {"server": "http://proxy.local:8080"}
    assert opts["locale"] == "fr-CA"
    assert writer.payloads[2]["cookies"][0]["domain"] == "example.com"
    assert writer.closed is True


@pytest.mark.asyncio
async def test_fetch_page_accepts_pydantic_action_enum(monkeypatch):
    reader = FakeReader([
        {"starsearch": "1.0", "compatible": True},
        {"ok": True, "sid": "sid-1", "result": {"sid": "sid-1", "egress": egress()}},
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

    html, _, descriptor = await starsearch_scrape._fetch_page(
        "127.0.0.1:7676",
        "https://example.com",
        actions=[action.model_dump()],
    )

    assert html == "<main>ok</main>"
    assert descriptor["gateway_enforced"] is True
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
        {"ok": True, "sid": "sid-1", "result": {"sid": "sid-1", "egress": egress()}},
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


@pytest.mark.asyncio
async def test_fetch_page_rejects_daemon_without_socket_egress_contract(monkeypatch):
    reader = FakeReader([
        {"starsearch": "1.0", "compatible": True},
        {"ok": True, "sid": "sid-1", "result": {"sid": "sid-1"}},
        {"ok": True, "sid": "sid-1", "result": {}},
    ])
    writer = FakeWriter()

    async def fake_open_connection(*args, **kwargs):
        return reader, writer

    monkeypatch.setattr(starsearch_scrape.asyncio, "open_connection", fake_open_connection)

    with pytest.raises(ValueError, match="missing its egress descriptor"):
        await starsearch_scrape._fetch_page("127.0.0.1:7676", "https://example.com")
    assert [payload.get("cmd") for payload in writer.payloads] == [
        None,
        "new_session",
        "close_session",
    ]


@pytest.mark.asyncio
async def test_fetch_page_rejects_mismatched_upstream_identity(monkeypatch):
    wrong = egress(proxied=True)
    wrong["upstream_identity"] = "f" * 64
    reader = FakeReader([
        {"starsearch": "1.0", "compatible": True},
        {"ok": True, "sid": "sid-1", "result": {"sid": "sid-1", "egress": wrong}},
        {"ok": True, "sid": "sid-1", "result": {}},
    ])
    writer = FakeWriter()

    async def fake_open_connection(*args, **kwargs):
        return reader, writer

    monkeypatch.setattr(starsearch_scrape.asyncio, "open_connection", fake_open_connection)

    with pytest.raises(ValueError, match="does not match its proxy lease"):
        await starsearch_scrape._fetch_page(
            "127.0.0.1:7676",
            "https://example.com",
            proxy={"server": "http://proxy.local:8080"},
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
