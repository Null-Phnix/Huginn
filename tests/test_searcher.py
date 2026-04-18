"""
Tests for BlackCrawl Searcher — Search engine configuration, result parsing.
"""

import pytest

from blackcrawl.searcher import Searcher, SEARCH_ENGINES


class TestSearchEngineConfig:
    """Test search engine configurations."""

    def test_all_engines_have_required_fields(self):
        """Each search engine config should have all required fields."""
        required_fields = ["url", "result_selector", "title_selector", "link_selector", "snippet_selector"]
        for name, config in SEARCH_ENGINES.items():
            for field in required_fields:
                assert field in config, f"{name} missing {field}"

    def test_engine_names(self):
        """Should have expected engine names."""
        assert "bing" in SEARCH_ENGINES
        assert "duckduckgo" in SEARCH_ENGINES
        assert "brave" in SEARCH_ENGINES

    def test_engine_urls_contain_query_placeholder(self):
        """Each engine URL template should have a {query} placeholder."""
        for name, config in SEARCH_ENGINES.items():
            assert "{query}" in config["url"], f"{name} URL missing {{query}} placeholder"

    def test_bing_url_format(self):
        """Bing URL should be properly formatted."""
        url = SEARCH_ENGINES["bing"]["url"].format(query="test+search", limit=10)
        assert "bing.com" in url
        assert "test+search" in url

    def test_duckduckgo_url_format(self):
        """DuckDuckGo URL should be properly formatted."""
        url = SEARCH_ENGINES["duckduckgo"]["url"].format(query="test+search", limit=10)
        assert "duckduckgo" in url
        assert "test+search" in url


class TestSearcherInit:
    """Test Searcher initialization."""

    def test_default_fallback_chain(self):
        searcher = Searcher(browser=None)
        assert searcher.fallback_chain is True

    def test_custom_fallback_chain(self):
        searcher = Searcher(browser=None, fallback_chain=False)
        assert searcher.fallback_chain is False