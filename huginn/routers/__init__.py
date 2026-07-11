"""Huginn API routers package."""

from .health import create_health_router
from .scrape import create_scrape_router
from .crawl import create_crawl_router
from .map import create_map_router
from .extract import create_extract_router
from .research import create_research_router
from .search import create_search_router
from .jobs import create_jobs_router
from .batch import create_batch_router
from .watch import create_watch_router
from .schedule import create_schedule_router
from .templates import create_templates_router
from .memory import create_memory_router
from .replay import create_replay_router
from .aliases import create_aliases_router
from .browser_sessions import create_browser_sessions_router

__all__ = [
    "create_health_router",
    "create_scrape_router",
    "create_crawl_router",
    "create_map_router",
    "create_extract_router",
    "create_research_router",
    "create_search_router",
    "create_jobs_router",
    "create_batch_router",
    "create_watch_router",
    "create_schedule_router",
    "create_templates_router",
    "create_memory_router",
    "create_replay_router",
    "create_aliases_router",
    "create_browser_sessions_router",
]
