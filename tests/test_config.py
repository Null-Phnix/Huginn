"""
Tests for Huginn configuration module.
"""

import os
import tempfile
from unittest.mock import patch

import pytest

from huginn.config import (
    HuginnConfig,
    BrowserConfig,
    CrawlConfig,
    ExtractConfig,
    ServerConfig,
    load_config,
)


class TestHuginnConfig:
    """Test HuginnConfig defaults and creation."""

    def test_default_config(self):
        """Config should have sensible defaults."""
        config = HuginnConfig()
        assert config.browser.backend == "playwright"
        assert config.browser.headless is True
        assert config.browser.stealth_mode is True
        assert config.crawl.max_depth == 3
        assert config.crawl.max_pages == 100
        assert config.crawl.concurrency == 5
        assert config.extract.llm_provider == "openai"
        assert config.extract.mental_model_enabled is True
        assert config.server.host == "127.0.0.1"
        assert config.server.port == 7432
        assert config.server.api_key is None
        assert config.browser.allow_playwright_fallback is False

    def test_custom_config(self):
        """Config should accept custom values."""
        config = HuginnConfig(
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
        config = HuginnConfig(data_dir="/tmp/test_bc")
        assert config.db_path == "/tmp/test_bc/huginn.db"

    def test_ensure_data_dir_creates_directory(self, tmp_path):
        """ensure_data_dir() should create data_dir if it doesn't exist."""
        data_dir = str(tmp_path / "new_dir")
        config = HuginnConfig(data_dir=data_dir)
        assert not os.path.isdir(data_dir)  # __post_init__ no longer creates dirs
        config.ensure_data_dir()
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
        assert isinstance(config, HuginnConfig)

    def test_env_var_overrides(self):
        """Environment variables should override config."""
        with patch.dict(os.environ, {
            "HUGINN_HOST": "127.0.0.9",
            "HUGINN_PORT": "9999",
            "HUGINN_HEADLESS": "false",
            "HUGINN_MAX_DEPTH": "7",
            "HUGINN_LLM_PROVIDER": "anthropic",
        }):
            config = load_config()
            assert config.server.host == "127.0.0.9"
            assert config.server.port == 9999
            assert config.browser.headless is False
            assert config.crawl.max_depth == 7
            assert config.extract.llm_provider == "anthropic"

    def test_playwright_fallback_requires_explicit_environment_opt_in(self):
        with patch.dict(
            os.environ, {"HUGINN_ALLOW_PLAYWRIGHT_FALLBACK": "true"}, clear=False
        ):
            config = load_config()

        assert config.browser.allow_playwright_fallback is True

    def test_env_data_dir_moves_derived_database_path(self):
        with patch.dict(os.environ, {"HUGINN_DATA_DIR": "/data/huginn"}, clear=False):
            config = load_config()

        assert config.data_dir == "/data/huginn"
        assert config.db_path == "/data/huginn/huginn.db"

    def test_explicit_env_database_path_wins_over_data_dir(self):
        with patch.dict(
            os.environ,
            {
                "HUGINN_DATA_DIR": "/data/huginn",
                "HUGINN_DB_PATH": "/state/custom.sqlite3",
            },
            clear=False,
        ):
            config = load_config()

        assert config.db_path == "/state/custom.sqlite3"

    def test_api_key_file_is_loaded_without_putting_secret_in_environment(
        self, monkeypatch, tmp_path
    ):
        secret_file = tmp_path / "api-key"
        secret_file.write_text("local-secret-key")
        monkeypatch.setenv("HUGINN_API_KEY_FILE", str(secret_file))
        monkeypatch.delenv("HUGINN_API_KEY", raising=False)

        config = load_config()

        assert config.server.api_key == "local-secret-key"

    def test_yaml_data_dir_moves_derived_database_path(self, tmp_path):
        import yaml

        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump({"data_dir": "/var/lib/huginn"}))

        config = load_config(str(config_file))

        assert config.db_path == "/var/lib/huginn/huginn.db"

    def test_yaml_explicit_database_path_is_preserved(self, tmp_path):
        import yaml

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump({"data_dir": "/var/lib/huginn", "db_path": "/state/jobs.sqlite3"})
        )

        config = load_config(str(config_file))

        assert config.db_path == "/state/jobs.sqlite3"


class TestUserAgentDefaults:
    """The default User-Agent must track the package version.

    huginn/__version__ is the source of truth. If a user constructs
    HuginnConfig() with no env override, BrowserConfig.user_agent must
    embed the current version (e.g. 'Huginn/1.2.0 (...)'), not a stale
    hardcoded 'Huginn/1.1' that drifted when the version was bumped.
    """

    def test_default_user_agent_contains_current_version(self):
        from huginn import __version__
        from huginn.config import HuginnConfig
        config = HuginnConfig()
        ua = config.browser.user_agent
        assert ua is not None, "user_agent default should not be None"
        assert __version__ in ua
        assert ua.startswith("Huginn/")

    def test_default_user_agent_does_not_have_stale_version(self):
        from huginn import __version__
        from huginn.config import HuginnConfig
        config = HuginnConfig()
        ua = config.browser.user_agent or ""
        # Must not contain a version that isn't the current one
        for stale in ("1.0.0", "1.1.0", "1.1"):
            assert f"/{stale}" not in ua or stale == __version__
