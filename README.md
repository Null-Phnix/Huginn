# BlackCrawl

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-9f6ff3?style=flat-square&labelColor=07061a)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-22d3ee?style=flat-square&labelColor=07061a)](https://opensource.org/licenses/MIT)
[![Version](https://img.shields.io/badge/version-v1.0.0-4ade80?style=flat-square&labelColor=07061a)](https://github.com/Null-Phnix/BlackCrawl)
[![Tests](https://img.shields.io/badge/tests-114_passing-4ade80?style=flat-square&labelColor=07061a)]

**Autonomous web scraping API. Firecrawl-compatible interface, stealth-first, self-hosted.**

Built on [Blackreach](https://github.com/Null-Phnix/Blackreach)'s DOM walker, stealth stack, and mental model system. No Redis, no Supabase — just Python, Playwright, and SQLite.

```bash
pip install blackcrawl
playwright install chromium
blackcrawl serve
```

```python
import httpx

# Scrape a page
resp = httpx.post("http://localhost:7432/v1/scrape", json={
    "url": "https://example.com",
    "formats": ["markdown", "html", "links"]
})

# Crawl a site
resp = httpx.post("http://localhost:7432/v1/crawl", json={
    "url": "https://docs.python.org",
    "maxDepth": 2,
    "limit": 50
})

# Map all URLs
resp = httpx.post("http://localhost:7432/v1/map", json={
    "url": "https://example.com"
})

# Extract structured data
resp = httpx.post("http://localhost:7432/v1/extract", json={
    "urls": ["https://example.com"],
    "prompt": "Extract the main heading and all paragraphs",
    "schema": {
        "type": "object",
        "properties": {
            "heading": {"type": "string"},
            "paragraphs": {"type": "array", "items": {"type": "string"}}
        }
    }
})

# Search and scrape
resp = httpx.post("http://localhost:7432/v1/search", json={
    "query": "Python async tutorial"
})
```

---

## Why BlackCrawl?

Every popular web scraping API has fundamental problems:

| Problem | Firecrawl | Jina Reader | BlackCrawl |
|---------|-----------|-------------|------------|
| Anti-bot detection | Cloud-only, paid tier | Basic | Built-in stealth (webdriver patches, fingerprint spoofing, behavioral humanization) |
| JS-rendered content | Playwright | Basic | Playwright + StarSearch (15 JS injection modules, 80+ fingerprint profiles) |
| Crawl reliability | Hangs silently | No crawling | Stuck detection, error recovery, rate limit resilience from 3k+ real-world test failures |
| Content quality | Readability + turndown | Readability | DOM walker (ARIA roles, landmarks, interactive element IDs — 200k token pages → 2k token observations) |
| Structured extraction | LLM throw at text | None | Mental model-assisted extraction with belief tracking and retry logic |
| Self-hosted features | Scrape + crawl only (extract/search are cloud-only) | Single pages | Every endpoint, fully self-hosted |
| Infrastructure | Node.js + Redis + Supabase | None | Python + SQLite — one process, one DB file |

## API Reference

All endpoints are Firecrawl-compatible. If it works with Firecrawl's API, it works with BlackCrawl.

### POST /v1/scrape

Scrape a single URL. Returns content in requested formats.

```json
{
  "url": "https://example.com",
  "formats": ["markdown", "html", "rawHtml", "screenshot", "links"],
  "onlyMainContent": true,
  "timeout": 30000,
  "waitFor": 2000,
  "actions": [
    {"type": "click", "selector": "#accept-cookies"},
    {"type": "wait", "amount": 1000}
  ]
}
```

Response:
```json
{
  "success": true,
  "data": {
    "markdown": "# Example Domain\n\nThis domain is for use in illustrative examples...",
    "html": "<main><h1>Example Domain</h1>...</main>",
    "metadata": {"url": "https://example.com", "title": "Example Domain", "language": "en"}
  }
}
```

### POST /v1/crawl

Crawl a site recursively. Returns a job ID for polling.

```json
{
  "url": "https://docs.python.org",
  "maxDepth": 3,
  "limit": 100,
  "allowBackwardCrawling": false,
  "allowExternalLinks": false,
  "includePaths": ["/3/"],
  "excludePaths": ["/3/library/"],
  "scrapeOptions": {
    "formats": ["markdown"],
    "onlyMainContent": true
  }
}
```

Response:
```json
{
  "success": true,
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "url": "/v1/crawl/550e8400-e29b-41d4-a716-446655440000"
}
```

Poll: `GET /v1/crawl/{id}` for status and results.

### POST /v1/map

Fast URL discovery. Returns all links without full content extraction.

```json
{
  "url": "https://example.com",
  "search": "api",
  "includeSubdomains": false,
  "limit": 5000
}
```

### POST /v1/extract

LLM-powered structured data extraction from URLs.

```json
{
  "urls": ["https://news.ycombinator.com"],
  "prompt": "Extract the top 5 story titles and their point counts",
  "schema": {
    "type": "object",
    "properties": {
      "stories": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "title": {"type": "string"},
            "points": {"type": "integer"}
          }
        }
      }
    }
  },
  "mentalModel": true
}
```

BlackCrawl extension: `mentalModel: true` enables belief tracking across extraction attempts. The LLM builds beliefs about page structure and uses them to improve retry accuracy.

### POST /v1/search

Web search with automatic result scraping. Falls back through Bing → DuckDuckGo → Brave.

```json
{
  "query": "Python async tutorial",
  "searchOptions": {"limit": 5},
  "scrapeOptions": {"formats": ["markdown"]},
  "fallbackChain": true
}
```

Response:
```json
{
  "success": true,
  "data": [
    {
      "markdown": "# Python Async IO...",
      "metadata": {"title": "Async IO in Python", "url": "https://...", "snippet": "..."}
    }
  ]
}
```

### Other Endpoints

- `GET /health` — Health check
- `GET /v1/jobs` — List all jobs
- `DELETE /v1/jobs/{id}` — Delete a job
- `DELETE /v1/crawl/{id}` — Cancel a crawl

---

## CLI

```bash
# Start API server
blackcrawl serve --port 8080

# Scrape a page
blackcrawl scrape https://example.com -f markdown -o output.json

# Crawl a site
blackcrawl crawl https://docs.python.org --depth 2 --limit 50 -o crawl.json

# Map URLs
blackcrawl map https://example.com --search api -o urls.json

# Search
blackcrawl search "Python async tutorial" --limit 5

# Health check
blackcrawl doctor
```

---

## Configuration

### Environment Variables

```bash
BLACKCRAWL_BROWSER_BACKEND=playwright   # or "starsearch"
BLACKCRAWL_HEADLESS=true
BLACKCRAWL_STEALTH=true                 # Enable anti-detection patches
BLACKCRAWL_MAX_DEPTH=3
BLACKCRAWL_MAX_PAGES=100
BLACKCRAWL_LLM_PROVIDER=openai         # openai, anthropic, google, ollama
BLACKCRAWL_LLM_MODEL=gpt-4o-mini       # Or leave empty for provider defaults
BLACKCRAWL_API_KEY=your-secret-key      # Optional Bearer token auth
BLACKCRAWL_PORT=7432
BLACKCRAWL_DATA_DIR=~/.blackcrawl
BLACKCRAWL_LOG_LEVEL=INFO
```

### Config File

```yaml
browser:
  backend: playwright  # or starsearch
  headless: true
  stealth_mode: true
  viewport_width: 1920
  viewport_height: 1080

crawl:
  max_depth: 3
  max_pages: 100
  concurrency: 5
  delay_between_requests: 1.0

extract:
  llm_provider: openai
  mental_model_enabled: true
  confidence_threshold: 0.7

server:
  host: 0.0.0.0
  port: 7432
  api_key: null
  job_ttl: 3600
```

---

## Architecture

```
Request → FastAPI → [Scrape|Crawl|Map|Extract|Search]
                          │
                    BrowserManager (Playwright + Stealth)
                          │
                   ┌──────┼──────┐
                   │      │      │
               DOM Walker  │   StarSearch
               (content    │   (Rust daemon,
                extraction) │    80+ fingerprints)
                          │
                    JobStore (SQLite)
                    (async job queue)
```

**Key design decisions:**

1. **Python + FastAPI** — not Node.js. Simpler deployment, fewer dependencies.
2. **SQLite, not Redis/Supabase** — one file, zero infra. Jobs, state, everything.
3. **Stealth-first** — webdriver patches, viewport spoofing, behavioral humanization baked in. Not a paid add-on.
4. **DOM Walker** — not raw HTML parsing. Semantic extraction with ARIA roles and interactive element IDs.
5. **Mental model extraction** — belief tracking across LLM retries. Not just throw LLM at text and hope.
6. **Firecrawl-compatible API** — drop-in replacement. Same endpoints, same request/response format.

---

## Differences from Firecrawl

| Feature | Firecrawl (self-hosted) | BlackCrawl |
|---------|------------------------|------------|
| Scrape | Yes | Yes |
| Crawl | Yes | Yes |
| Map | Yes | Yes |
| Extract | Cloud only | **Yes — self-hosted** |
| Search | Cloud only | **Yes — self-hosted** |
| Anti-bot | Cloud only | **Yes — built in** |
| Infrastructure | Node + Redis + Postgres | Python + SQLite |
| LLM extraction | OpenAI only | **OpenAI, Anthropic, Google, Ollama** |
| Mental model | No | **Yes — belief tracking + retry** |
| Stuck detection | No | **Yes — from 3k+ real-world failures** |

---

## Installation

```bash
pip install blackcrawl
playwright install chromium
```

**With LLM extraction:**
```bash
pip install "blackcrawl[openai]"      # or anthropic, google, all
```

**From source:**
```bash
git clone https://github.com/Null-Phnix/BlackCrawl
cd BlackCrawl
pip install -e ".[dev]"
playwright install chromium
```

---

## License

MIT — use it however you want. No Elastic License restrictions, no cloud-only features.

---

Built with fury by [Phnix](https://github.com/Null-Phnix). Powered by [Blackreach](https://github.com/Null-Phnix/Blackreach)'s stealth engine.