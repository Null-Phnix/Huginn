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


class TestScrollConfig:
    """Test ScrollConfig for infinite scroll handling."""

    def test_scroll_config_defaults(self):
        from huginn.browser import ScrollConfig
        config = ScrollConfig()
        assert config.max_scrolls == 10
        assert config.delay_ms == 500
        assert config.scroll_to_bottom is True

    def test_scroll_config_custom(self):
        from huginn.browser import ScrollConfig
        config = ScrollConfig(max_scrolls=5, delay_ms=1000, scroll_to_bottom=False)
        assert config.max_scrolls == 5
        assert config.delay_ms == 1000
        assert config.scroll_to_bottom is False

    def test_scroll_config_max_scrolls_clamped(self):
        from huginn.browser import ScrollConfig
        import pytest
        # max_scrolls must be >= 1 — Pydantic enforces this
        with pytest.raises(Exception):
            ScrollConfig(max_scrolls=0)

    def test_scroll_config_delay_ms_min(self):
        from huginn.browser import ScrollConfig
        # delay_ms must be >= 0
        config = ScrollConfig(delay_ms=0)
        assert config.delay_ms >= 0


class TestAutoScroll:
    """Test auto_scroll method on BrowserManager."""

    @pytest.mark.asyncio
    async def test_auto_scroll_returns_count(self):
        """auto_scroll should return number of scrolls performed."""
        from huginn.browser import BrowserManager, ScrollConfig

        bm = BrowserManager()
        page = AsyncMock()
        # Simulate a page that grows for 3 scrolls then stabilizes.
        # auto_scroll calls evaluate("document.body.scrollHeight") to read height,
        # then evaluate("window.scrollTo(...)") to scroll down. The mock must
        # distinguish between these by checking if the script starts with "document".
        call_count = [0]
        heights = [5000, 10000, 15000, 15000]  # growing then stops

        async def mock_evaluate(script):
            if script.startswith("document"):
                idx = min(call_count[0], len(heights) - 1)
                call_count[0] += 1
                return heights[idx]
            # scrollTo calls — just return None
            return None

        page.evaluate = AsyncMock(side_effect=mock_evaluate)

        config = ScrollConfig(max_scrolls=5, delay_ms=10)
        result = await bm.auto_scroll(page, config)

        # Should have scrolled 3 times (then height stabilized)
        assert result == 3

    @pytest.mark.asyncio
    async def test_auto_scroll_stops_on_no_growth(self):
        """auto_scroll should stop when page height stops growing."""
        from huginn.browser import BrowserManager, ScrollConfig

        bm = BrowserManager()
        page = AsyncMock()
        # Page doesn't grow at all (immediate stop)
        page.evaluate = AsyncMock(return_value=5000)

        config = ScrollConfig(max_scrolls=5, delay_ms=10)
        result = await bm.auto_scroll(page, config)

        # Should stop after first scroll since height never changes
        assert result == 1

    @pytest.mark.asyncio
    async def test_auto_scroll_max_scrolls_limit(self):
        """auto_scroll should not exceed max_scrolls."""
        from huginn.browser import BrowserManager, ScrollConfig

        bm = BrowserManager()
        page = AsyncMock()
        # Page keeps growing forever
        counter = [0]
        async def growing_height(script):
            counter[0] += 1
            return 5000 * (counter[0] + 1)

        page.evaluate = AsyncMock(side_effect=growing_height)

        config = ScrollConfig(max_scrolls=3, delay_ms=10)
        result = await bm.auto_scroll(page, config)

        assert result <= 3

    @pytest.mark.asyncio
    async def test_auto_scroll_default_config(self):
        """auto_scroll with no config should use defaults."""
        from huginn.browser import BrowserManager

        bm = BrowserManager()
        page = AsyncMock()
        # Immediate no-growth should complete fast
        page.evaluate = AsyncMock(return_value=5000)

        result = await bm.auto_scroll(page)

        assert result >= 1

    @pytest.mark.asyncio
    async def test_auto_scroll_scrolls_back_to_top(self):
        """auto_scroll should scroll back to top when scroll_to_bottom is True."""
        from huginn.browser import BrowserManager, ScrollConfig

        bm = BrowserManager()
        page = AsyncMock()
        # Immediately stop growing
        page.evaluate = AsyncMock(return_value=5000)

        config = ScrollConfig(max_scrolls=2, delay_ms=10, scroll_to_bottom=True)
        await bm.auto_scroll(page, config)

        # Last evaluate call should scroll back to top
        # The evaluate calls: scrollHeight, scrollTo, scrollHeight, scrollTo, scrollTo(0,0)
        calls = [str(c) for c in page.evaluate.call_args_list]
        assert any("scrollTo(0, 0)" in str(c) or "scrollTo(0,0)" in str(c) for c in calls)

    @pytest.mark.asyncio
    async def test_auto_scroll_no_scroll_to_top(self):
        """When scroll_to_bottom=False, should NOT scroll back to top."""
        from huginn.browser import BrowserManager, ScrollConfig

        bm = BrowserManager()
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value=5000)

        config = ScrollConfig(max_scrolls=2, delay_ms=10, scroll_to_bottom=False)
        await bm.auto_scroll(page, config)

        # None of the evaluate calls should be scrollTo(0, 0) or scrollTo(0,0)
        calls = [str(c) for c in page.evaluate.call_args_list]
        # Note: window.scrollTo(0, document.body.scrollHeight) IS the scroll action
        # scrollTo(0, 0) is the "back to top" — there should be only scrollDown calls, no top reset
        top_scrolls = [c for c in calls if "0, 0" in c or "0,0" in c]
        # The scroll-to-bottom action is "window.scrollTo(0, document.body.scrollHeight)"
        # which does NOT have "0, 0" — it has "0, ...scrollHeight"
        # The back-to-top is "window.scrollTo(0, 0)" which has "0, 0"
        assert len(top_scrolls) == 0

class TestEnhancedActions:
    """Test new action types: select, hover, wait_for_selector."""

    def test_action_type_select(self):
        from huginn.models import ActionType
        assert ActionType.SELECT == "select"

    def test_action_type_hover(self):
        from huginn.models import ActionType
        assert ActionType.HOVER == "hover"

    def test_action_type_wait_for_selector(self):
        from huginn.models import ActionType
        assert ActionType.WAIT_FOR_SELECTOR == "wait_for_selector"

    def test_action_model_select(self):
        from huginn.models import Action, ActionType
        action = Action(type=ActionType.SELECT, selector="select#country", values=["US"])
        assert action.type == ActionType.SELECT
        assert action.selector == "select#country"
        assert action.values == ["US"]

    def test_action_model_hover(self):
        from huginn.models import Action, ActionType
        action = Action(type=ActionType.HOVER, selector="div.menu-item")
        assert action.type == ActionType.HOVER
        assert action.selector == "div.menu-item"

    def test_action_model_wait_for_selector(self):
        from huginn.models import Action, ActionType
        action = Action(type=ActionType.WAIT_FOR_SELECTOR, selector="div.loaded", timeout=5000)
        assert action.type == ActionType.WAIT_FOR_SELECTOR
        assert action.selector == "div.loaded"
        assert action.timeout == 5000

    @pytest.mark.asyncio
    async def test_execute_actions_select(self):
        """Test that select action calls page.select_option."""
        from huginn.browser import BrowserManager
        bm = BrowserManager.__new__(BrowserManager)
        bm._playwright = None
        bm._browser = None
        bm._contexts = []
        bm.config = None

        page = AsyncMock()
        page.select_option = AsyncMock()

        actions = [{"type": "select", "selector": "select#country", "values": ["US"]}]
        await bm.execute_actions(page, actions)

        page.select_option.assert_called_once_with("select#country", ["US"])

    @pytest.mark.asyncio
    async def test_execute_actions_hover(self):
        """Test that hover action calls page.hover."""
        from huginn.browser import BrowserManager
        bm = BrowserManager.__new__(BrowserManager)
        bm._playwright = None
        bm._browser = None
        bm._contexts = []
        bm.config = None

        page = AsyncMock()
        page.hover = AsyncMock()

        actions = [{"type": "hover", "selector": "div.menu-item"}]
        await bm.execute_actions(page, actions)

        page.hover.assert_called_once_with("div.menu-item")

    @pytest.mark.asyncio
    async def test_execute_actions_wait_for_selector(self):
        """Test that wait_for_selector action calls page.wait_for_selector."""
        from huginn.browser import BrowserManager
        bm = BrowserManager.__new__(BrowserManager)
        bm._playwright = None
        bm._browser = None
        bm._contexts = []
        bm.config = None

        page = AsyncMock()
        page.wait_for_selector = AsyncMock()

        actions = [{"type": "wait_for_selector", "selector": "div.loaded", "timeout": 5000}]
        await bm.execute_actions(page, actions)

        page.wait_for_selector.assert_called_once_with("div.loaded", timeout=5000)

    @pytest.mark.asyncio
    async def test_execute_actions_wait_for_selector_default_timeout(self):
        """Test wait_for_selector with no explicit timeout uses default."""
        from huginn.browser import BrowserManager
        bm = BrowserManager.__new__(BrowserManager)
        bm._playwright = None
        bm._browser = None
        bm._contexts = []
        bm.config = None

        page = AsyncMock()
        page.wait_for_selector = AsyncMock()

        actions = [{"type": "wait_for_selector", "selector": "div.loaded"}]
        await bm.execute_actions(page, actions)

        # Default timeout should be 10000ms
        page.wait_for_selector.assert_called_once_with("div.loaded", timeout=10000)

    @pytest.mark.asyncio
    async def test_execute_actions_select_with_value_string(self):
        """Test select action with single value string."""
        from huginn.browser import BrowserManager
        bm = BrowserManager.__new__(BrowserManager)
        bm._playwright = None
        bm._browser = None
        bm._contexts = []
        bm.config = None

        page = AsyncMock()
        page.select_option = AsyncMock()

        actions = [{"type": "select", "selector": "select#lang", "value": "en"}]
        await bm.execute_actions(page, actions)

        page.select_option.assert_called_once_with("select#lang", "en")

    @pytest.mark.asyncio
    async def test_execute_actions_hover_fails_gracefully(self):
        """Test that hover failure doesn't crash the action sequence."""
        from huginn.browser import BrowserManager
        bm = BrowserManager.__new__(BrowserManager)
        bm._playwright = None
        bm._browser = None
        bm._contexts = []
        bm.config = None

        page = AsyncMock()
        page.hover = AsyncMock(side_effect=Exception("Element not found"))

        # Should not raise, just log warning
        actions = [{"type": "hover", "selector": "div.nonexistent"}]
        await bm.execute_actions(page, actions)

        # Hover was attempted even though it failed
        page.hover.assert_called_once()
