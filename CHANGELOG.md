# Changelog

All notable changes to Huginn are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.0.0] - 2026-04-17

### What Changed

Huginn is a new project — a Firecrawl-compatible web scraping API built on
Blackreach's stealth browser engine. This is the initial release.

### Added

- **5 API endpoints** — `/v1/scrape`, `/v1/crawl`, `/v1/map`, `/v1/extract`, `/v1/search`
- **Firecrawl-compatible API** — same endpoint structure, same request/response format
- **Stealth browser** — Playwright with anti-detection patches (webdriver, viewport, CDP)
- **DOM Walker** — semantic HTML extraction with ARIA roles, landmark detection, interactive element IDs
- **Mental model extraction** — belief tracking + retry logic across LLM extraction attempts
- **Multi-provider LLM** — OpenAI, Anthropic, Google Gemini, Ollama (local)
- **Search fallback chain** — Bing → DuckDuckGo → Brave
- **Job queue** — SQLite-backed async job queue for crawl and extract (no Redis needed)
- **CLI** — `blackcrawl scrape`, `crawl`, `map`, `search`, `serve`, `doctor`
- **114 tests** — unit + integration, all passing against real sites
- **Challenge detection** — Cloudflare, DDoS-Guard auto-wait
- **Sitemap parsing** — robots.txt + sitemap.xml discovery for fast mapping
- **Path filtering** — include/exclude glob patterns for crawl scope control
- **Optional auth** — Bearer token API key (set `HUGINN_API_KEY`)
- **Docker-ready** — single process, single DB file

### What This Is Built On

- **Blackreach** v5.0.0-beta.1 — stealth Playwright, DOM walker, stuck detection (3k+ regression tests)
- **StarSearch** — Rust stealth daemon (15 JS injection modules, 80+ fingerprint profiles)
- **FastAPI** — async Python web framework
- **SQLite + aiosqlite** — job queue and state storage