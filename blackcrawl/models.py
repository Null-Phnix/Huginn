"""
BlackCrawl API Models — Pydantic v2 schemas for all endpoints.

Firecrawl-compatible interface with BlackCrawl extensions.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, HttpUrl, field_validator


# ─── Enums ────────────────────────────────────────────────────────────────────

class OutputFormat(str, Enum):
    MARKDOWN = "markdown"
    HTML = "html"
    RAW_HTML = "rawHtml"
    SCREENSHOT = "screenshot"
    LINKS = "links"
    METADATA = "metadata"


class ActionType(str, Enum):
    CLICK = "click"
    WAIT = "wait"
    SCROLL = "scroll"
    SCREENSHOT = "screenshot"
    TYPE = "type"
    PRESS = "press"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProxyMode(str, Enum):
    BASIC = "basic"
    STEALTH = "stealth"


# ─── Shared Models ─────────────────────────────────────────────────────────────

class Action(BaseModel):
    """Browser action to execute before extraction."""
    type: ActionType
    selector: Optional[str] = None
    text: Optional[str] = None
    key: Optional[str] = None
    direction: Optional[str] = "down"  # for scroll
    amount: Optional[int] = None  # pixels for scroll, ms for wait


class Location(BaseModel):
    """Geographic location for proxy."""
    country: Optional[str] = None
    languages: Optional[List[str]] = None


class ExtractOptions(BaseModel):
    """LLM extraction options."""
    model_config = {"populate_by_name": True}

    prompt: Optional[str] = None
    schema_: Optional[Dict[str, Any]] = Field(None, alias="schema")
    system_prompt: Optional[str] = Field(None, alias="systemPrompt")


class ScrapeOptions(BaseModel):
    """Options passed to scrape during crawl."""
    model_config = {"populate_by_name": True}

    formats: List[OutputFormat] = Field(default_factory=lambda: [OutputFormat.MARKDOWN])
    only_main_content: bool = Field(True, alias="onlyMainContent")
    include_tags: Optional[List[str]] = Field(None, alias="includeTags")
    exclude_tags: Optional[List[str]] = Field(None, alias="excludeTags")
    wait_for: Optional[int] = Field(None, alias="waitFor")
    timeout: int = 30000
    headers: Optional[Dict[str, str]] = None
    actions: Optional[List[Action]] = None


# ─── Scrape Endpoint ──────────────────────────────────────────────────────────

class ScrapeRequest(BaseModel):
    """POST /v1/scrape request body."""
    model_config = {"populate_by_name": True}

    url: str
    formats: List[OutputFormat] = Field(default_factory=lambda: [OutputFormat.MARKDOWN])
    headers: Optional[Dict[str, str]] = None
    wait_for: Optional[int] = Field(None, alias="waitFor")
    actions: Optional[List[Action]] = None
    extract: Optional[ExtractOptions] = None
    include_tags: Optional[List[str]] = Field(None, alias="includeTags")
    exclude_tags: Optional[List[str]] = Field(None, alias="excludeTags")
    only_main_content: bool = Field(True, alias="onlyMainContent")
    timeout: int = 30000
    proxy: Optional[ProxyMode] = None
    location: Optional[Location] = None
    # BlackCrawl extensions
    stealth_mode: bool = Field(True, alias="stealthMode")


class ScrapeData(BaseModel):
    """Scrape result data."""
    model_config = {"populate_by_name": True}

    markdown: Optional[str] = None
    html: Optional[str] = None
    raw_html: Optional[str] = Field(None, alias="rawHtml")
    screenshot: Optional[str] = None  # base64
    links: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    extract: Optional[Dict[str, Any]] = None


class ScrapeResponse(BaseModel):
    """POST /v1/scrape response."""
    success: bool
    data: Optional[ScrapeData] = None
    error: Optional[str] = None


# ─── Crawl Endpoint ───────────────────────────────────────────────────────────

class CrawlRequest(BaseModel):
    """POST /v1/crawl request body."""
    model_config = {"populate_by_name": True}

    url: str
    max_depth: Optional[int] = Field(None, alias="maxDepth")
    limit: Optional[int] = None  # max pages
    allow_backward_crawling: bool = Field(False, alias="allowBackwardCrawling")
    allow_external_links: bool = Field(False, alias="allowExternalLinks")
    include_paths: Optional[List[str]] = Field(None, alias="includePaths")
    exclude_paths: Optional[List[str]] = Field(None, alias="excludePaths")
    scrape_options: Optional[ScrapeOptions] = Field(None, alias="scrapeOptions")
    stream: bool = Field(False, alias="stream")


class CrawlStartResponse(BaseModel):
    """POST /v1/crawl response (async job started)."""
    success: bool
    id: str
    url: Optional[str] = None
    error: Optional[str] = None


class CrawlStatusResponse(BaseModel):
    """GET /v1/crawl/{id} response."""
    model_config = {"populate_by_name": True}

    success: bool
    status: JobStatus
    completed: int = 0
    total: Optional[int] = None
    expires_at: Optional[datetime] = Field(None, alias="expiresAt")
    data: Optional[List[ScrapeData]] = None
    error: Optional[str] = None


# ─── Map Endpoint ─────────────────────────────────────────────────────────────

class MapRequest(BaseModel):
    """POST /v1/map request body."""
    model_config = {"populate_by_name": True}

    url: str
    search: Optional[str] = None
    include_subdomains: bool = Field(False, alias="includeSubdomains")
    limit: int = 5000


class MapResponse(BaseModel):
    """POST /v1/map response."""
    success: bool
    links: List[str] = Field(default_factory=list)
    error: Optional[str] = None


# ─── Extract Endpoint ─────────────────────────────────────────────────────────

class ExtractRequest(BaseModel):
    """POST /v1/extract request body."""
    model_config = {"populate_by_name": True}

    urls: List[str] = Field(default_factory=list)
    prompt: Optional[str] = None
    schema_: Optional[Dict[str, Any]] = Field(None, alias="schema")
    system_prompt: Optional[str] = Field(None, alias="systemPrompt")
    # BlackCrawl extensions
    mental_model: bool = Field(True, alias="mentalModel")
    max_retries: int = 3
    stream: bool = Field(False, alias="stream")

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, v):
        if not v:
            raise ValueError("urls must contain at least one URL")
        return v


class ExtractStartResponse(BaseModel):
    """POST /v1/extract response (async)."""
    success: bool
    id: str
    error: Optional[str] = None


class ExtractStatusResponse(BaseModel):
    """GET /v1/extract/{id} response."""
    success: bool
    status: JobStatus
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None



# ─── SSE Streaming Responses ─────────────────────────────────────────────────

class StreamCrawlResponse(BaseModel):
    """SSE event payload for crawl document/done events."""
    type: str  # "document" or "done"
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class StreamExtractResponse(BaseModel):
    """SSE event payload for extract progress/done events."""
    type: str  # "progress" or "done"
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None



# ─── SSE Streaming Responses ─────────────────────────────────────────────────

class StreamCrawlResponse(BaseModel):
    """SSE event payload for crawl document/done events."""
    type: str  # "document" or "done"
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class StreamExtractResponse(BaseModel):
    """SSE event payload for extract progress/done events."""
    type: str  # "progress" or "done"
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# ─── Search Endpoint ──────────────────────────────────────────────────────────

class SearchOptions(BaseModel):
    """Search-specific options."""
    model_config = {"populate_by_name": True}

    limit: int = 5
    tbs: Optional[str] = None  # time range
    country: Optional[str] = None
    language: Optional[str] = None


class SearchRequest(BaseModel):
    """POST /v1/search request body."""
    model_config = {"populate_by_name": True}

    query: str
    search_options: Optional[SearchOptions] = Field(None, alias="searchOptions")
    scrape_options: Optional[ScrapeOptions] = Field(None, alias="scrapeOptions")
    # BlackCrawl extension
    fallback_chain: bool = Field(True, alias="fallbackChain")  # Bing->DDG->Brave


class SearchResultItem(BaseModel):
    """Single search result with scraped content."""
    markdown: Optional[str] = None
    html: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class SearchResponse(BaseModel):
    """POST /v1/search response."""
    success: bool
    data: List[SearchResultItem] = Field(default_factory=list)
    error: Optional[str] = None



# ─── Batch Scrape Endpoint ─────────────────────────────────────────────────────

class BatchScrapeRequest(BaseModel):
    """POST /v1/batch/scrape request body."""
    model_config = {"populate_by_name": True}

    urls: List[str] = Field(..., min_length=1, max_length=50)
    formats: List[OutputFormat] = Field(default_factory=lambda: [OutputFormat.MARKDOWN])
    only_main_content: bool = Field(True, alias="onlyMainContent")
    include_tags: Optional[List[str]] = Field(None, alias="includeTags")
    exclude_tags: Optional[List[str]] = Field(None, alias="excludeTags")
    timeout: int = 30000

    @field_validator("urls")
    @classmethod
    def validate_urls_count(cls, v):
        if len(v) > 50:
            raise ValueError("Maximum 50 URLs per batch")
        return v


class BatchScrapeResultItem(BaseModel):
    """Single result in a batch scrape response."""
    url: str
    success: bool
    data: Optional[ScrapeData] = None
    error: Optional[str] = None


class BatchScrapeResponse(BaseModel):
    """POST /v1/batch/scrape response."""
    success: bool
    data: List[BatchScrapeResultItem] = Field(default_factory=list)
    error: Optional[str] = None


# ─── Job Management �──────────────────────────────────────────────────────────

class JobInfo(BaseModel):
    """Job status info."""
    id: str
    type: str  # "crawl" or "extract"
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    completed: int = 0
    total: Optional[int] = None
    error: Optional[str] = None

