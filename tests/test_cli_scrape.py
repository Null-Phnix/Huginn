"""Regressions for `huginn scrape` bugs found by suitebench."""

import json
import logging
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from huginn.cli import cli
from huginn.models import ScrapeData

runner = CliRunner()


def _invoke(args, data):
    """Run `huginn scrape` with the browser and scraper mocked to return `data`."""
    scraper = AsyncMock()
    scraper.scrape.return_value = data
    with patch("huginn.cli._get_browser", AsyncMock()), patch("huginn.scraper.Scraper", return_value=scraper):
        return runner.invoke(cli, ["scrape", "https://example.com", *args]), scraper


OK = ScrapeData(markdown="# Hello " + "word " * 40, metadata={"status_code": 200, "backend": "playwright"})
FAILED = ScrapeData(metadata={"url": "https://example.com", "status_code": 404, "error": "HTTP 404"})


def test_comma_separated_formats_are_accepted():
    result, scraper = _invoke(["-f", "markdown,metadata"], OK)
    assert result.exit_code == 0, result.output
    assert [f.value for f in scraper.scrape.call_args.kwargs["formats"]] == ["markdown", "metadata"]


def test_unknown_format_is_a_usage_error():
    result, _ = _invoke(["-f", "markdown,bogus"], OK)
    assert result.exit_code == 2
    assert "bogus" in result.output


def test_failed_scrape_exits_nonzero_and_still_writes_json(tmp_path):
    out = tmp_path / "out.json"
    result, _ = _invoke(["-o", str(out)], FAILED)
    assert result.exit_code == 1
    assert json.loads(out.read_text())["metadata"]["status_code"] == 404


def test_json_to_non_tty_stdout_is_plain_and_parseable():
    result, _ = _invoke(["-F", "json"], OK)
    assert result.exit_code == 0
    # CliRunner stdout is not a TTY: the whole output must be one JSON document, unwrapped.
    assert json.loads(result.stdout)["markdown"] == OK.markdown


def _mock_browser(navigate_ok, status):
    browser = AsyncMock()
    browser.last_status_code = status
    browser.new_context.return_value = AsyncMock()
    browser.new_page.return_value = AsyncMock()
    browser.navigate.return_value = navigate_ok
    browser.extract_content.return_value = {"url": "https://example.com", "title": "T", "description": "", "language": "en"}
    browser.to_markdown.return_value = "# Hello"
    return browser


@pytest.mark.asyncio
async def test_http_error_status_is_preserved():
    from huginn.scraper import Scraper
    result = await Scraper(_mock_browser(False, 404)).scrape("https://example.com")
    assert result.metadata["status_code"] == 404
    assert result.metadata["error"] == "HTTP 404"


@pytest.mark.asyncio
async def test_no_response_is_still_navigation_failed():
    from huginn.scraper import Scraper
    result = await Scraper(_mock_browser(False, 0)).scrape("https://example.com")
    assert result.metadata["status_code"] == 500
    assert result.metadata["error"] == "Navigation failed"


@pytest.mark.asyncio
async def test_backend_is_reported_and_missing_starsearch_warns(monkeypatch, caplog):
    from huginn.scraper import Scraper
    monkeypatch.delenv("HUGINN_STARSEARCH_TCP", raising=False)
    with caplog.at_level(logging.WARNING):
        result = await Scraper(_mock_browser(True, 200)).scrape("https://example.com", render_mode="full")
    assert result.metadata["backend"] == "playwright"
    assert "StarSearch is not configured (set HUGINN_STARSEARCH_TCP)" in caplog.text
