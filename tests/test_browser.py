"""
Tests for BlackCrawl Browser Manager — Unit tests for pure logic.
Browser-dependent tests are in integration tests.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blackcrawl.browser import BrowserManager, STEALTH_INIT_JS


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
        from blackcrawl.browser import CHALLENGE_WAIT_SECONDS, MAX_CHALLENGE_RETRIES
        assert CHALLENGE_WAIT_SECONDS > 0
        assert MAX_CHALLENGE_RETRIES > 0