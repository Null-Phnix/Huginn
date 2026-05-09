# Huginn — Odin's Raven

**Self-hosted web scraping, crawling, and extraction API. Stealth-first. Open source. No cloud tier.**

[![Version](https://img.shields.io/badge/version-1.2.0-blue)](https://github.com/Null-Phnix/Huginn)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> Huginn (Old Norse: "thought") is one of Odin's ravens — he flies across the world and brings back information.

---

## What It Does

Huginn is a drop-in, self-hosted alternative to [Firecrawl](https://firecrawl.dev) and [ScrapingBee](https://scrapingbee.com). Run it on your own hardware. No API keys. No rate limits from a cloud provider. No paywall.

- **Scrape** any URL → Markdown, HTML, links, screenshots, metadata
- **Crawl** entire sites recursively with depth limits and dedup
- **Map** site structure → sitemap-like URL lists
- **Extract** structured data using LLM-guided templates (product, article, job, etc.)
- **Research** multi-hop deep dives with vector memory persistence
- **Watch** pages for content changes with webhook notifications
- **Batch** process 100s of URLs concurrently

---

## Quick Start

```bash
# Install
pip install huginn

# Start the API server
huginn serve

# Scrape a URL
curl -X POST http://localhost:8000/v1/probe \
  -H "Authorization: Bearer demo-key" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "formats": ["markdown"]}'

# Crawl a site (stream results as NDJSON)
curl -X POST http://localhost:8000/v1/sweep \
  -H "Authorization: Bearer demo-key" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://docs.python.org/3", "stream": true, "format": "jsonl"}'

# Watch a page for changes
curl -X POST http://localhost:8000/v1/watch \
  -H "Authorization: Bearer demo-key" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://news.ycombinator.com", "webhook_url": "https://myapp.com/webhook"}'
```

---

## Why Huginn vs Firecrawl?

| | Huginn | Firecrawl |
|---|---|---|
| **Hosting** | Self-hosted (your box) | Cloud-only |
| **Cost** | Free (your compute) | $0.005/page + tiers |
| **Stealth** | Playwright + stealth patches | Varies |
| **Change Detection** | Built-in watch daemon | Not available |
| **Streaming** | SSE + NDJSON real-time | SSE only |
| **Research Memory** | ChromaDB vector persistence | Not available |
| **Open Source** | ✅ MIT | Partial |

---

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   FastAPI   │────▶│   Scraper    │────▶│  Playwright │
│  REST API   │     │  (concurrent)│     │   Browser   │
└─────────────┘     └──────────────┘     └─────────────┘
      │                    │
      ▼                    ▼
┌─────────────┐     ┌──────────────┐
│   Crawler   │     │   Extractor  │────▶ LLM (optional)
│ (BFS pool)  │     │  (templates) │
└─────────────┘     └──────────────┘
      │
      ▼
┌─────────────┐
│   Watcher   │────▶ Webhooks on change
│  (daemon)   │
└─────────────┘
```

---

## Benchmarks

Deterministic crawl throughput (fake scraper, no network):

| Graph | Workers | Pages | Time | Pages/sec |
|-------|---------|-------|------|-----------|
| Chain (depth 50) | 3 | 50 | 0.06s | **899** |
| Tree (branching 3, depth 3) | 5 | 40 | 0.05s | **737** |
| Star (hub + 100 leaves) | 5 | 101 | 0.06s | **1671** |

Peak memory: **< 0.1 MB** for 100-page crawls.

Run your own: `python benchmarks/bench.py`

---

## Installation

```bash
pip install huginn

# Or from source
git clone https://github.com/Null-Phnix/Huginn.git
cd Huginn
pip install -e ".[all]"

# Install Playwright browser
playwright install chromium
```

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/probe` | POST | Scrape a single URL |
| `/v1/sweep` | POST | Start async crawl (SSE / NDJSON) |
| `/v1/sweep/{id}` | GET | Get crawl status |
| `/v1/chart` | POST | Map site URLs (sitemap) |
| `/v1/graph` | POST | BFS site graph (nodes + edges) |
| `/v1/distill` | POST | Structured LLM extraction |
| `/v1/seek` | POST | Web search |
| `/v1/research` | POST | Deep multi-hop research |
| `/v1/flock` | POST | Batch URL processing |
| `/v1/watch` | POST | Start page change detection |
| `/v1/watch/{url}` | GET | Watch status |
| `/v1/watch/{url}/check` | POST | Manual check |
| `/v1/watch/{url}` | DELETE | Stop watching |
| `/v1/schedule` | POST | Cron/interval scheduling |

---

## CLI

```bash
huginn scrape <url> --format markdown
huginn crawl <url> --depth 3 --limit 50
huginn map <url> --limit 5000
huginn extract <url> --template product
huginn watch add <url> --webhook https://myapp.com/hook
huginn watch check <url>
huginn search <query>
huginn serve --port 8000
huginn doctor
```

---

## Environment

```bash
export HUGINN_API_KEY="your-key"
export HUGINN_PORT=8000
export HUGINN_LOG_LEVEL=INFO
export HUGINN_BROWSER_HEADLESS=true
export HUGINN_BROWSER_STEALTH=true
```

---

## License

MIT — see [LICENSE](LICENSE)

---

Built with ❤️ by [Phnix](https://github.com/Null-Phnix)
