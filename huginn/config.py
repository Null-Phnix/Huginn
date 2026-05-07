"""
Huginn Configuration

Loaded from environment variables or config file.
No Redis, no Supabase — just SQLite and environment.
"""

import os
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path


@dataclass
class BrowserConfig:
    """Browser backend configuration."""
    backend: str = "playwright"  # "playwright" or "starsearch"
    headless: bool = True
    starsearch_socket: Optional[str] = None  # auto-detect if None
    viewport_width: int = 1920
    viewport_height: int = 1080
    navigation_timeout: int = 30000  # ms
    wait_for_timeout: int = 5000  # ms
    stealth_mode: bool = True
    user_agent: Optional[str] = "Huginn/1.1 (+https://huginn.dev/bot)"


@dataclass
class ProxyConfig:
    """Proxy configuration."""
    server: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None


@dataclass
class CrawlConfig:
    """Crawl behavior configuration."""
    max_depth: int = 3
    max_pages: int = 100
    concurrency: int = 5
    delay_between_requests: float = 1.0  # seconds
    respect_robots_txt: bool = True
    follow_sitemaps: bool = True
    allow_external_links: bool = False
    allow_backward_crawling: bool = False
    deduplicate_urls: bool = True


@dataclass
class ExtractConfig:
    """Extraction configuration."""
    llm_provider: str = "openai"  # "openai", "anthropic", "google", "ollama"
    llm_model: Optional[str] = None  # auto-pick per provider
    max_retries: int = 3
    mental_model_enabled: bool = True
    confidence_threshold: float = 0.7


@dataclass
class ServerConfig:
    """API server configuration."""
    host: str = "0.0.0.0"
    port: int = 7432
    api_key: Optional[str] = None  # Bearer token auth (optional)
    job_ttl: int = 3600  # seconds to keep completed jobs
    max_concurrent_jobs: int = 10
    request_timeout: int = 300  # seconds
    rate_limit: str = "100/minute"  # slowapi rate limit string


@dataclass
class HuginnConfig:
    """Master configuration."""
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    crawl: CrawlConfig = field(default_factory=CrawlConfig)
    extract: ExtractConfig = field(default_factory=ExtractConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    data_dir: str = os.path.expanduser("~/.huginn")
    db_path: str = ""  # derived from data_dir if empty
    log_level: str = "INFO"

    def __post_init__(self):
        if not self.db_path:
            self.db_path = os.path.join(self.data_dir, "huginn.db")
        # NOTE: Directory creation deferred to runtime (ensure_data_dir).
        # __post_init__ should not have side effects like makedirs.
        _apply_env(self)

    def ensure_data_dir(self):
        """Create data directory if it does not exist. Call at startup, not import time."""
        os.makedirs(self.data_dir, exist_ok=True)


def load_config(config_path: Optional[str] = None) -> HuginnConfig:
    """Load configuration from file and environment variables."""
    config = HuginnConfig()

    # Load from file if provided
    if config_path and os.path.exists(config_path):
        try:
            import yaml
        except ImportError:
            raise ImportError("PyYAML is required to load config files. Install with: pip install pyyaml")
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        _merge_config(config, data)

    # Environment variable overrides
    _apply_env(config)

    return config


def _merge_config(config: HuginnConfig, data: dict):
    """Merge dict data into config object."""
    if "browser" in data:
        for k, v in data["browser"].items():
            if hasattr(config.browser, k):
                setattr(config.browser, k, v)
    if "crawl" in data:
        for k, v in data["crawl"].items():
            if hasattr(config.crawl, k):
                setattr(config.crawl, k, v)
    if "extract" in data:
        for k, v in data["extract"].items():
            if hasattr(config.extract, k):
                setattr(config.extract, k, v)
    if "server" in data:
        for k, v in data["server"].items():
            if hasattr(config.server, k):
                setattr(config.server, k, v)
    if "proxy" in data:
        for k, v in data["proxy"].items():
            if hasattr(config.proxy, k):
                setattr(config.proxy, k, v)
    if "data_dir" in data:
        config.data_dir = data["data_dir"]
    if "log_level" in data:
        config.log_level = data["log_level"]


def _apply_env(config: HuginnConfig):
    """Apply environment variable overrides."""
    env_map = {
        "HUGINN_BROWSER_BACKEND": ("browser", "backend"),
        "HUGINN_HEADLESS": ("browser", "headless"),
        "HUGINN_STEALTH": ("browser", "stealth_mode"),
        "HUGINN_MAX_DEPTH": ("crawl", "max_depth"),
        "HUGINN_MAX_PAGES": ("crawl", "max_pages"),
        "HUGINN_CONCURRENCY": ("crawl", "concurrency"),
        "HUGINN_LLM_PROVIDER": ("extract", "llm_provider"),
        "HUGINN_LLM_MODEL": ("extract", "llm_model"),
        "HUGINN_MENTAL_MODEL": ("extract", "mental_model_enabled"),
        "HUGINN_API_KEY": ("server", "api_key"),
        "HUGINN_PORT": ("server", "port"),
        "HUGINN_RATE_LIMIT": ("server", "rate_limit"),
        "HUGINN_DATA_DIR": (None, "data_dir"),
        "HUGINN_LOG_LEVEL": (None, "log_level"),
        "HUGINN_PROXY_SERVER": ("proxy", "server"),
        "HUGINN_PROXY_USERNAME": ("proxy", "username"),
        "HUGINN_PROXY_PASSWORD": ("proxy", "password"),
        "HUGINN_USER_AGENT": ("browser", "user_agent"),
    }
    for env_var, (section, attr) in env_map.items():
        val = os.environ.get(env_var)
        if val is not None:
            target = getattr(config, section) if section else config
            current = getattr(target, attr)
            # Type coerce (safe — log warning on bad values)
            try:
                if isinstance(current, bool):
                    val = val.lower() in ("true", "1", "yes")
                elif isinstance(current, int):
                    val = int(val)
                elif isinstance(current, float):
                    val = float(val)
            except (ValueError, AttributeError) as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Invalid value for {env_var}={os.environ.get(env_var)!r}: {e}"
                )
                continue
            setattr(target, attr, val)