"""Test fixtures and shared utilities for Huginn tests."""

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test data."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def temp_db(temp_dir):
    """Path to a temporary SQLite database."""
    return os.path.join(temp_dir, "test_huginn.db")


@pytest.fixture
def mock_page():
    """Create a mock Playwright Page."""
    page = AsyncMock()
    page.title = AsyncMock(return_value="Test Page")
    page.url = "https://example.com"
    page.goto = AsyncMock(return_value=MagicMock(ok=True))
    page.content = AsyncMock(return_value="<html><body><h1>Hello</h1><p>World</p></body></html>")
    page.evaluate = AsyncMock(return_value={
        "title": "Test Page",
        "url": "https://example.com",
        "description": "A test page",
        "language": "en",
        "links": [
            {"href": "https://example.com/about", "text": "About", "rel": ""},
            {"href": "https://example.com/contact", "text": "Contact", "rel": ""},
        ],
        "interactive": [],
        "text": "Hello World This is a test page with some content.",
        "headings": [{"level": 1, "text": "Hello", "id": ""}],
        "meta": {"description": "A test page"},
    })
    page.screenshot = AsyncMock(return_value=b"fake_screenshot_data")
    page.wait_for_load_state = AsyncMock()
    page.keyboard = AsyncMock()
    page.fill = AsyncMock()
    page.click = AsyncMock()
    page.close = AsyncMock()
    return page


@pytest.fixture
def mock_context(mock_page):
    """Create a mock Playwright BrowserContext."""
    context = AsyncMock()
    context.new_page = AsyncMock(return_value=mock_page)
    context.close = AsyncMock()
    context.set_extra_http_headers = AsyncMock()
    return context


@pytest.fixture
def mock_browser(mock_context):
    """Create a mock Playwright Browser."""
    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=mock_context)
    browser.close = AsyncMock()
    return browser


@pytest.fixture
def mock_playwright(mock_browser):
    """Create a mock Playwright instance."""
    pw = AsyncMock()
    pw.chromium.launch = AsyncMock(return_value=mock_browser)
    pw.stop = AsyncMock()
    return pw