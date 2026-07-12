"""
Huginn API — Firecrawl-compatible web scraping service.

Autonomous, stealth-first, self-hosted.
Built on StarSearch + Blackreach's DOM walker + mental model.

Run:
    huginn serve                    # Start with defaults
    huginn serve --port 8080        # Custom port
    huginn serve --config path     # Custom config
    uvicorn huginn.api:app         # Direct uvicorn
"""

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from . import __version__, _branding
from .browser import BrowserManager
from .config import HuginnConfig
from .job_store import JobStore

# ─── Module-level LLM helpers (re-exported from huginn.llm) ───────────────────
# The actual implementation lives in huginn.llm for testability and reuse.
# Re-exported here so existing callers (e.g. tests) can keep importing
# from huginn.api without breaking.
from .llm import (  # noqa: E402, F401
    _LLM_PROVIDER_CONFIG,  # back-compat alias
    LLM_PROVIDER_CONFIG,
    _summarize_text,  # back-compat alias
    summarize_text,
)
from .metrics import MetricsMiddleware
from .proxy import build_proxy_provider
from .replay_log import ReplayLog
from .scheduler import Scheduler
from .state import get_state, limiter, reset_state
from .tasks import register_scheduler_handlers
from .utils import make_verify_api_key

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    state = get_state()
    config = app.state.config

    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Ensure data directory exists
    config.ensure_data_dir()

    # Initialize job store
    state.job_store = JobStore(config.db_path)
    await state.job_store.init()

    # Initialize replay log (shares the same data directory)
    state.replay_log = ReplayLog(config.db_path)
    await state.replay_log.init()

    # Initialize browser
    state.browser = BrowserManager(config=config.browser)
    await state.browser.start()

    # Initialize explicit egress policy. A configured provider failing to load
    # aborts startup instead of silently sending traffic directly.
    state.proxy_provider = build_proxy_provider(config)

    # Initialize scheduler
    state.scheduler = Scheduler(state.job_store)
    state.scheduler.start()
    logger.info("Scheduler started")
    register_scheduler_handlers()

    # Initialize page watcher (shared singleton for all watch endpoints)
    from .watcher import PageWatcher, get_watch_store
    state.watcher = PageWatcher(state.browser, get_watch_store())
    logger.info("Page watcher initialized")

    logger.info(f"Huginn started on {config.server.host}:{config.server.port}")
    logger.info(f"Browser: backend={state.browser.backend}, headless={config.browser.headless}, stealth={config.browser.stealth_mode}")

    yield

    # Cleanup
    logger.info("Shutting down Huginn...")
    for task in state.crawl_tasks.values():
        task.cancel()
    # Stop all watch monitoring tasks
    if state.watcher:
        for url, task in list(state.watcher._monitor_tasks.items()):
            task.cancel()
            logger.info(f"Stopped monitoring {url}")
    from .routers.browser_sessions import close_all_browser_sessions
    browser_session_drain = await close_all_browser_sessions()
    if browser_session_drain["failed"]:
        logger.warning(
            "StarSearch browser-session drain incomplete: %s",
            browser_session_drain,
        )
    elif browser_session_drain["closed"]:
        logger.info("Closed tracked StarSearch browser sessions: %s", browser_session_drain)
    await state.browser.stop()
    await state.job_store.close()
    if state.replay_log:
        await state.replay_log.close()
    if state.scheduler:
        await state.scheduler.stop()
        logger.info("Scheduler stopped")

    # Reset state for next run
    reset_state()


def create_app(config: Optional[HuginnConfig] = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if config is None:
        config = HuginnConfig()

    # Populate state so routers can access config
    state = get_state()
    state.config = config
    state.proxy_provider = build_proxy_provider(config)

    app = FastAPI(
        title=_branding.name,
        description=f"{_branding.name} — {_branding.description}. Firecrawl-compatible, stealth-first, self-hosted.",
        version=__version__,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "Health", "description": "Server health, readiness, and metrics"},
            {"name": "Scrape", "description": "Single-page scraping in multiple formats (markdown, HTML, links, screenshot)"},
            {"name": "Crawl", "description": "Recursive site crawling with depth limits and job tracking"},
            {"name": "Map", "description": "URL discovery and site mapping"},
            {"name": "Extract", "description": "Structured data extraction with templates or LLM prompts"},
            {"name": "Search", "description": "Web search and search-driven scraping"},
            {"name": "Batch", "description": "Batch / flock operations for multiple URLs"},
            {"name": "Watch", "description": "Page change detection and monitoring"},
            {"name": "Schedule", "description": "Scheduled / recurring scraping jobs"},
            {"name": "Research", "description": "Deep autonomous research with memory and citations"},
            {"name": "Memory", "description": "ChromaDB research memory query and management"},
            {"name": "Templates", "description": "Extraction template registry"},
            {"name": "Jobs", "description": "Job lifecycle management (list, cancel)"},
            {"name": "Replay", "description": "Scrape replay log / audit trail"},
            {"name": "Browser Sessions", "description": "Authenticated StarSearch browser session lifecycle and commands"},
        ],
    )

    app.state.config = config

    # Browser-origin access is disabled by default. A wildcard plus anonymous
    # local API would let any visited website drive localhost endpoints.
    cors_origins = [
        origin.strip()
        for origin in config.server.cors_origins.split(",")
        if origin.strip()
    ]
    if "*" in cors_origins and not config.server.api_key:
        raise RuntimeError(
            "HUGINN_CORS_ORIGINS=* requires HUGINN_API_KEY or HUGINN_API_KEY_FILE"
        )
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Metrics middleware (must be added before routes)
    app.add_middleware(MetricsMiddleware)

    # Rate limiting
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Create verify_api_key dependency bound to this app's config
    verify_api_key = make_verify_api_key(config)

    # ─── Register Routers ─────────────────────────────────────────────────────

    from .routers import (
        create_aliases_router,
        create_batch_router,
        create_browser_sessions_router,
        create_crawl_router,
        create_extract_router,
        create_health_router,
        create_jobs_router,
        create_map_router,
        create_memory_router,
        create_replay_router,
        create_research_router,
        create_schedule_router,
        create_scrape_router,
        create_search_router,
        create_templates_router,
        create_watch_router,
    )

    app.include_router(create_health_router(verify_api_key))
    app.include_router(create_scrape_router(config, verify_api_key))
    app.include_router(create_crawl_router(config, verify_api_key))
    app.include_router(create_map_router(config, verify_api_key))
    app.include_router(create_extract_router(config, verify_api_key))
    app.include_router(create_research_router(config, verify_api_key))
    app.include_router(create_search_router(config, verify_api_key))
    app.include_router(create_jobs_router(config, verify_api_key))
    app.include_router(create_batch_router(config, verify_api_key))
    app.include_router(create_watch_router(config, verify_api_key))
    app.include_router(create_schedule_router(config, verify_api_key))
    app.include_router(create_templates_router(config, verify_api_key))
    app.include_router(create_memory_router(config, verify_api_key))
    app.include_router(create_replay_router(config, verify_api_key))
    app.include_router(create_aliases_router(config, verify_api_key))
    app.include_router(create_browser_sessions_router(config, verify_api_key))

    return app


# ─── Default app instance ────────────────────────────────────────────────────

# Create a default app instance so `uvicorn huginn.api:app` works.
# For custom config, use create_app(config) or the CLI: `huginn serve`
app = create_app()


def get_app(config: Optional[HuginnConfig] = None) -> FastAPI:
    """Get or create the FastAPI app instance."""
    global app
    if config is not None:
        app = create_app(config)
    return app
