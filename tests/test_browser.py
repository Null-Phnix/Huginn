"""
Tests for Huginn Browser Manager — Unit tests for pure logic.
Browser-dependent tests are in integration tests.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from huginn.browser import BrowserManager, STEALTH_INIT_JS


class TestBrowserManager:
    """Test BrowserManager initialization and configuration."""

    def test_init_defaults(self):
        bm = BrowserManager()
        assert bm.headless is True
        assert bm.stealth is True
        assert bm.navigation_timeout == 30000
        assert bm.viewport == (1920, 1080)

    def test_init_custom(self):
        bm = BrowserManager(
            headless=False,
            stealth=False,
            navigation_timeout=60000,
            viewport=(1280, 720),
            user_agent="CustomAgent/1.0",
        )
        assert bm.headless is False
        assert bm.stealth is False
        assert bm.navigation_timeout == 60000
        assert bm.viewport == (1280, 720)
        assert bm.user_agent == "CustomAgent/1.0"


class TestStealthJS:
    """Test the stealth initialization JavaScript."""

    def test_patches_webdriver(self):
        assert "navigator.webdriver" in STEALTH_INIT_JS

    def test_patches_chrome(self):
        assert "chrome" in STEALTH_INIT_JS

    def test_patches_plugins(self):
        assert "plugins" in STEALTH_INIT_JS

    def test_patches_permissions(self):
        assert "permissions" in STEALTH_INIT_JS

    def test_patches_languages(self):
        assert "languages" in STEALTH_INIT_JS


class TestBrowserConstants:
    """Test browser behavior constants."""

    def test_challenge_wait(self):
        from huginn.browser import CHALLENGE_WAIT_SECONDS, MAX_CHALLENGE_RETRIES
        assert CHALLENGE_WAIT_SECONDS > 0
        assert MAX_CHALLENGE_RETRIES > 0


class TestWaitStrategy:
    """Test smart wait strategy parsing and enum."""

    def test_wait_strategy_enum(self):
        from huginn.browser import WaitStrategy
        assert WaitStrategy.SELECTOR == "selector"
        assert WaitStrategy.NETWORK_IDLE == "networkidle"
        assert WaitStrategy.DOM_CONTENT_LOADED == "domcontentloaded"
        assert WaitStrategy.TIMEOUT == "timeout"

    def test_parse_wait_for_int(self):
        from huginn.browser import WaitStrategy, parse_wait_for
        result = parse_wait_for(3000)
        assert result == (WaitStrategy.TIMEOUT, 3000)

    def test_parse_wait_for_float(self):
        from huginn.browser import WaitStrategy, parse_wait_for
        result = parse_wait_for(2500.5)
        assert result == (WaitStrategy.TIMEOUT, 2500)

    def test_parse_wait_for_string_selector(self):
        from huginn.browser import WaitStrategy, parse_wait_for
        result = parse_wait_for("div.content")
        assert result == (WaitStrategy.SELECTOR, "div.content")

    def test_parse_wait_for_networkidle(self):
        from huginn.browser import WaitStrategy, parse_wait_for
        result = parse_wait_for("networkidle")
        assert result == (WaitStrategy.NETWORK_IDLE, 5000)

    def test_parse_wait_for_network_idle_hyphen(self):
        from huginn.browser import WaitStrategy, parse_wait_for
        result = parse_wait_for("network-idle")
        assert result == (WaitStrategy.NETWORK_IDLE, 5000)

    def test_parse_wait_for_network_idle_underscore(self):
        from huginn.browser import WaitStrategy, parse_wait_for
        result = parse_wait_for("network_idle")
        assert result == (WaitStrategy.NETWORK_IDLE, 5000)

    def test_parse_wait_for_domcontentloaded(self):
        from huginn.browser import WaitStrategy, parse_wait_for
        result = parse_wait_for("domcontentloaded")
        assert result == (WaitStrategy.DOM_CONTENT_LOADED, 0)

    def test_parse_wait_for_none(self):
        from huginn.browser import WaitStrategy, parse_wait_for
        result = parse_wait_for(None)
        assert result == (WaitStrategy.TIMEOUT, 3000)

    def test_parse_wait_for_css_class_selector(self):
        from huginn.browser import WaitStrategy, parse_wait_for
        result = parse_wait_for("#main-content")
        assert result == (WaitStrategy.SELECTOR, "#main-content")