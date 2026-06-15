# HUGINN Upgrade Plan — v1.1 Competitive Sprint

**Goal**: Close the feature gap with Firecrawl and establish HUGINN as the credible self-hosted alternative.

**Philosophy**: Stability and correctness over feature velocity. Every shipped feature must have tests. No SaaS-only tiers. Everything works self-hosted.

---

## Phase 1: Foundation Fixes (v1.1-alpha)

*Make what we have rock-solid before building on top of it.*

### 1.1 Smart Page Rendering
**File**: `browser.py`
**Problem**: We get past Cloudflare but can't wait for SPAs to load. `wait_for` only handles numeric timeouts, not CSS selectors or networkIdle.
**Plan**:
- Add `waitForSelector` support to `navigate()` — accept string selectors, not just ints
- Add `networkIdle` detection — wait until no new network requests for 500ms
- Add infinite scroll detection and auto-scroll option
- Add `waitFor` enum: `timeout | selector | networkIdle | domContentLoaded`
**Tests**: Test selector-based wait, test networkIdle with mock page, test infinite scroll detection

### 1.2 Crawl Intelligence
**File**: `crawler.py`
**Problem**: Our crawler is dumb BFS. No duplicate detection, no priority, no pagination awareness.
**Plan**:
- Content hashing — hash page content (SHA-256 of cleaned text), skip if duplicate seen
- Priority queue — replace simple deque with heapq; prioritize same-domain, shorter paths
- Pagination detection — detect `/page/2`, `?page=2`, `next >` link patterns
- robots.txt parsing — respect robots.txt by default, add `ignore_robots` flag
- Graceful shutdown — handle SIGINT during crawl, save progress
**Tests**: Test content hashing skip, test priority ordering, test pagination detection, test robots.txt parsing

### 1.3 Error Recovery
**Files**: `scraper.py`, `crawler.py`, `api.py`
**Problem**: We crash on network errors. No retries, no backoff, no graceful degradation.
**Plan**:
- Retry with exponential backoff on timeout/connection errors (max 3 retries)
- Classify errors: TimeoutError→408, ConnectionError→502, HTTP 4xx→passthrough, HTTP 5xx→502
- Partial result returns — if a page partially loaded, return what we have with a warning
- Rate limit feedback — if site returns 429, back off and retry after delay
**Tests**: Test retry logic, test error classification, test partial result, test 429 handling

---

## Phase 2: Killer Feature (v1.1-beta)

*Build the #1 reason people pay for Firecrawl.*

### 2.1 Structured Extraction with Schema
**Files**: New `schema_extractor.py`, updates to `extractor.py`, `api.py`, `models.py`
**Problem**: Our distill endpoint extracts to text/markdown. No structured output. Firecrawl's #1 feature is "define a schema, get JSON back."
**Plan**:
- Accept Pydantic model as schema in `/v1/distill` request
- Two-pass extraction: (1) mental model DOM walk, (2) LLM extraction against schema
- Validate output against the schema — if validation fails, retry with clearer prompt (max 2 retries)
- Return structured JSON matching the schema, not just text
- Support optional `format: "text" | "markdown" | "json"` — json requires schema field
- For local LLM (Ollama): pass `format: "json"` + schema in system prompt. For OpenAI/Anthropic: use structured output / tool use
**Schema**:
```python
class DistillRequest(BaseModel):
    urls: List[str]                    # one or more URLs
    schema_: Optional[dict] = None     # JSON schema or Pydantic model dump
    prompt: Optional[str] = None        # natural language extraction hint
    format: str = "markdown"           # "text" | "markdown" | "json"
    llm_provider: Optional[str] = None
    stream: bool = False
```
**Tests**: Test schema validation, test retry on failed extraction, test text/markdown/json output modes, test with each LLM provider

### 2.2 Actions API
**Files**: New `actions.py`, updates to `browser.py`, `api.py`, `models.py`
**Problem**: Can't interact with pages before scraping. Can't fill forms, click buttons, scroll to load more.
**Plan**:
- Define action types: `click`, `type`, `scroll`, `wait`, `screenshot`, `select`
- Accept ordered list of actions in probe/sweep requests
- Execute actions sequentially before content extraction
- Add to `/v1/probe` and `/v1/sweep` request models as optional `actions` field
**Schema**:
```python
class BrowserAction(BaseModel):
    type: Literal["click", "type", "scroll", "wait", "screenshot", "select"]
    selector: Optional[str] = None      # CSS selector for click/type/select
    value: Optional[str] = None         # text to type, option value, scroll amount
    timeout: Optional[int] = 5000       # max wait in ms
```
**Tests**: Test action sequence execution, test selector not found error, test type action, test scroll action

---

## Phase 3: Production Readiness (v1.1-rc)

*Make HUGINN something people can actually depend on.*

### 3.1 Docker One-Liner
**Problem**: Firecrawl's Docker is famously broken. Ours must be trivial.
**Plan**:
- Write clean `Dockerfile` — multi-stage build, minimal image
- `docker run -p 7432:7432 -e HUGINN_API_KEY=xxx huginn/huginn`
- Include Playwright install in image
- Docker Compose for Redis-optional setup
- Test: `docker build && docker run && curl /health`
- **No pnpm, no microservice architecture, no Bull queue**

### 3.2 Webhook Notifications
**Files**: New `webhooks.py`, updates to `job_store.py`, `api.py`, `models.py`
**Problem**: No way to know when a long-running job finishes without polling.
**Plan**:
- Add `webhook_url` field to sweep/distill/flock requests
- On job completion, POST result summary to webhook URL
- Retry webhook delivery up to 3 times with backoff
- Log webhook delivery status
**Tests**: Test webhook POST on job completion, test retry on failure, test URL validation

### 3.3 Built-in Scheduling
**Files**: New `scheduler.py`, updates to `api.py`, `models.py`
**Problem**: Users want recurring crawls. No self-hosted tool offers this well.
**Plan**:
- Add `/v1/schedule` endpoint — create recurring probe/sweep/chart jobs
- Crontab-like scheduling: interval, cron expression, or simple recurring
- Store schedules in SQLite alongside jobs
- Background scheduler thread checks and fires jobs
- API to list/pause/resume/delete schedules
**Tests**: Test schedule creation, test cron parsing, test pause/resume, test schedule fires job

### 3.4 PDF/OCR Extraction
**Files**: Updates to `scraper.py`
**Problem**: Can't extract content from PDFs. Firecrawl has had this open since Oct 2024.
**Plan**:
- Detect PDF content-type in scraper response
- Route to pdfminer or PyMuPDF for text extraction
- Add OCR option using pytesseract for scanned PDFs
- Return extracted text as markdown
**Tests**: Test PDF detection, test text extraction from PDF, test OCR fallback

---

## Phase 4: Ecosystem (v1.2)

*Build the developer experience that drives adoption.*

### 4.1 Python SDK Client
- Thin wrapper around httpx
- Type-hinted, async-first
- Matches our endpoint naming: `client.probe()`, `client.sweep()`, `client.chart()`, etc.
- Schema extraction: `client.distill(schema=MyModel)`

### 4.2 OpenAPI Spec
- Auto-generate from FastAPI models (we already have them)
- Publish to huginn.dev (or GitHub Pages)
- Enables SDK generation in any language

### 4.3 MCP Server
- Firecrawl's MCP is a major adoption driver
- Build HUGINN MCP server exposing probe/sweep/chart/seek/distill as tools
- Package as `huginn-mcp` pip installable

### 4.4 Identifiable User-Agent
- Set `Huginn/Bot (+https://huginn.dev/bot)` as default user-agent
- Make it configurable: `Huginn/Bot (+{contact_url})` or custom
- Include link to bot policy page in UA string
- Be a good web citizen — the ethical alternative

---

## Not Prioritized (Explicitly Deferred)

| Feature | Why Deferred |
|---------|-------------|
| Web dashboard | Large effort, not a differentiator. CLI + API is fine for v1. |
| Screenshot support | Firecrawl gates this. We can add later, not ship-stopper. |
| Headful browser mode | Niche use case. Stealth headless covers most needs. |
| Rate limiting as a service | Self-hosted users control their own rate. |
| Multi-user auth | Self-hosted = single user/team. Enterprise auth later. |

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Docker one-liner | Works first try, no config |
| Structured extraction | Matches Firecrawl's quality with local LLMs |
| Crawl accuracy | 95%+ pages correctly rendered on top 1000 sites |
| Self-host reliability | 7-day uptime without restart |
| Test coverage | 200+ tests (currently 134) |
| GitHub stars (6 months) | 1000+ |