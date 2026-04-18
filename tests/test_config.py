"""
Tests for BlackCrawl configuration module.
"""

import os
import tempfile
from unittest.mock import patch

import pytest

from blackcrawl.config import (
    BlackCrawlConfig,
    BrowserConfig,
    CrawlConfig,
    ExtractConfig,
    ServerConfig,
    load_config,
)


class TestBlackCrawlConfig:
    """Test BlackCrawlConfig defaults and creation."""

    def test_default_config(self):
        """Config should have sensible defaults."""
        config = BlackCrawlConfig()
        assert config.browser.backend == "playwright"
        assert config.browser.headless is True
        assert config.browser.stealth_mode is True
        assert config.crawl.max_depth == 3
        assert config.crawl.max_pages == 100
        assert config.crawl.concurrency == 5
        assert config.extract.llm_provider == "openai"
        assert config.extract.mental_model_enabled is True
        assert config.server.host == "0.0.0.0"
        assert config.server.port == 7432
        assert config.server.api_key is None

    def test_custom_config(self):
        """Config should accept custom values."""
        config = BlackCrawlConfig(
            browser=BrowserConfig(headless=False, stealth_mode=False),
            crawl=CrawlConfig(max_depth=5, max_pages=200),
            server=ServerConfig(port=8080, api_key="test123"),
        )
        assert config.browser.headless is False
        assert config.browser.stealth_mode is False
        assert config.crawl.max_depth == 5
        assert config.crawl.max_pages == 200
        assert config.server.port == 8080
        assert config.server.api_key == "test123"

    def test_post_init_creates_db_path(self):
        """db_path should be derived from data_dir if not set."""
        config = BlackCrawlConfig(data_dir="/tmp/test_bc")
        assert config.db_path == "/tmp/test_bc/blackcrawl.db"

    def test_post_init_creates_data_dir(self, tmp_path):
        """Should create data_dir if it doesn't exist."""
        data_dir = str(tmp_path / "new_dir")
        config = BlackCrawlConfig(data_dir=data_dir)
        assert os.path.isdir(data_dir)


class TestLoadConfig:
    """Test config loading from file and env vars."""

    def test_load_from_yaml(self, tmp_path):
        """Should load config from YAML file."""
        import yaml

        config_file = tmp_path / "config.yaml"
        config_data = {
            "browser": {"headless": False, "stealth_mode": False},
            "crawl": {"max_depth": 10, "max_pages": 500},
            "server": {"port": 9090},
        }
        config_file.write_text(yaml.dump(config_data))

        config = load_config(str(config_file))
        assert config.browser.headless is False
        assert config.browser.stealth_mode is False
        assert config.crawl.max_depth == 10
        assert config.crawl.max_pages == 500
        assert config.server.port == 9090

    def test_load_from_nonexistent_file(self):
        """Should return defaults for nonexistent file."""
        config = load_config("/nonexistent/config.yaml")
        assert isinstance(config, BlackCrawlConfig)

    def test_env_var_overrides(self):
        """Environment variables should override config."""
        with patch.dict(os.environ, {
            "BLACKCRAWL_PORT": "9999",
            "BLACKCRAWL_HEADLESS": "false",
            "BLACKCRAWL_MAX_DEPTH": "7",
            "BLACKCRAWL_LLM_PROVIDER": "anthropic",
        }):
            config = load_config()
            assert config.server.port == 9999
            assert config.browser.headless is False
            assert config.crawl.max_depth == 7
            assert config.extract.llm_provider == "anthropic"