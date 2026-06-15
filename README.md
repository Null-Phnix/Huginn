# Huginn — Odin's Raven 🜏

> *Huginn (Old Norse: "thought") is one of Odin's ravens — he flies across the world and brings back information.*

[![Version](https://img.shields.io/badge/version-1.2.0-7c3aed?style=flat-square&labelColor=07061a)](https://github.com/Null-Phnix/Huginn)
[![Python](https://img.shields.io/badge/python-3.10%2B-4ade80?style=flat-square&labelColor=07061a)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-348_passing-22d3ee?style=flat-square&labelColor=07061a)](tests/)
[![License](https://img.shields.io/badge/license-MIT-facc15?style=flat-square&labelColor=07061a)](LICENSE)

**Self-hosted web scraping, crawling, and extraction API. Stealth-first. Open source. No cloud tier.**

---

## Table of Contents

- [Why I Built This](#why-i-built-this)
- [What It Does](#what-it-does)
- [Current Pain Points](#current-pain-points)
- [End Goals](#end-goals--where-this-is-headed)
- [Quick Start](#quick-start)
- [Why Huginn vs Firecrawl?](#why-huginn-vs-firecrawl)
- [Architecture](#architecture)
- [Benchmarks](#benchmarks)
- [CLI](#cli)
- [API Endpoints](#api-endpoints)
- [Templates](#templates)
- [Installation](#installation)
- [Environment](#environment)
- [License](#license)

---

## Why I Built This

Huginn started as **BlackCrawl** — a stripped-down Blackreach focused on structured data extraction. I needed to scrape mythology texts for Bifrost (my knowledge base project) and Firecrawl wanted $0.005 per page. For a 10,000-page crawl that's $50. Per crawl. I do this weekly.

The rebrand to Huginn wasn't just a name change — it was a product positioning shift. BlackCrawl was trying to be everything (autonomous agent + scraper + researcher). Huginn is specifically a **self-hosted Firecrawl alternative**: structured scraping API with a REST interface, CLI, templates, and streaming output. Nothing more, nothing less.

Blackreach handles "go find me state space model papers from 2024." Huginn handles "scrape this product page and give me structured JSON with price, availability, and specs." Both use the same Playwright stealth backend, but Huginn is the API you call, Blackreach is the agent you delegate to.

---

## What It Does

| Feature | What It Does |
|---------|-------------|
| **Scrape** | Any URL → Markdown, HTML, links, screenshots, metadata |
| **Crawl** | Entire sites recursively with depth limits, dedup, robots.txt respect |
| **Map** | Site structure → sitemap-like URL lists with BFS graph (nodes + edges) |
| **Extract** | Structured data via LLM-guided templates (10 built-in schemas) |
| **Research** | Multi-hop deep dives with ChromaDB vector memory persistence |
| **Watch** | Page change detection with webhook notifications |
| **Batch** | 100s of URLs processed concurrently |
| **Stream** | Real-time NDJSON or SSE output during crawls |

---

## Current Pain Points

These are the battles I'm actively fighting:

1. **Per-page pricing is a scam** — Firecrawl at $0.005/page sounds cheap until you crawl 50,000 pages. That's $250. Per month if you do it weekly. My electricity costs less than that.

2. **The `robotparser` from stdlib is garbage** — No wildcard support, no caching, no async. I wrapped it in an async layer with caching, but it's still a liability. If a site uses complex `robots.txt` rules, Huginn might miss pages or over-crawl.

3. **LLMs return garbage JSON half the time** — Even GPT-4o sometimes outputs malformed JSON with trailing commas, unescaped newlines, or markdown wrappers. I built a 6-step repair pipeline (direct parse → bracket matching → regex extraction → auto-repair → graceful fallback), but it's defensive code I wish I didn't need.

4. **Playwright stealth is an arms race** — Sites add new fingerprinting every month. The `playwright-stealth` package helps but it's not magic. Some sites still detect automation and serve different content. I patch what I can, but it's whack-a-mole.

5. **SPA vs static site detection** — Every page needs the right wait strategy. `domContentLoaded` is fast but misses SPA content. `networkIdle` is thorough but slow. `selector` waits are precise but require knowing the selector in advance. Huginn defaults to `networkIdle` which is safe but slow. Auto-detecting the right strategy per-site is unsolved.

6. **Memory grows with crawl size** — ChromaDB research memory and SQLite job queue both grow unbounded. For a 10,000-page research crawl, the vector DB can hit 2GB+. I need auto-pruning or pagination, but it's not built yet.

---

## End Goals — Where This Is Headed

### Short Term (now → 3 months)
- **Auto wait strategy detection** — analyze page structure and choose `domContentLoaded` vs `networkIdle` vs `selector` automatically
- **Better robots.txt parser** — replace stdlib `robotparser` with something that handles wildcards and caches properly
- **Memory pruning** — auto-cleanup of old research reports and vector chunks
- **Huginn ↔ Blackreach state sharing** — both tools should share the same research memory and crawl history

### Medium Term (3–6 months)
- **Unified agent swarm** — Huginn feeds scraped data into Blackreach research, which feeds findings into Deep Video Watcher comprehension, all sharing one ChromaDB instance
- **Persistent knowledge graph** — every scrape and crawl adds to a cumulative understanding, not isolated reports
- **Self-healing extraction** — when a template fails, the system tries alternative schemas or asks the user, not just returns garbage

### Long Term (6–12 months)
- **Fully autonomous research pipeline** — "Find every paper on X from the last 2 years, extract key findings, compare with my existing knowledge, flag contradictions, write a summary"
- **Distributed crawling** — split large crawls across my Linux desktop and MacBook, one brain across two machines
- **Zero cloud dependencies** — every feature that currently needs an API key (LLM extraction) should have a local fallback

---

## Quick Start

```bash
# Install
pip install huginn

# Start the API server
huginn serve

# Or use the interactive CLI
huginn
```

```bash
# Scrape a URL
curl -X POST http://localhost:7432/v1/probe \
  -H "Authorization: Bearer $HUGINN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "formats": ["markdown"]}'

# Crawl a site (stream results as NDJSON)
curl -X POST http://localhost:7432/v1/sweep \
  -H "Authorization: Bearer $HUGINN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://docs.python.org/3", "stream": true, "format": "jsonl"}'

# Watch a page for changes
curl -X POST http://localhost:7432/v1/watch \
  -H "Authorization: Bearer $HUGINN_API_KEY" \
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
| **Graph Mapping** | BFS nodes + edges | Not available |
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
┌─────────────┐     ┌──────────────┐
│   Watcher   │────▶│    Memory    │
│  (daemon)   │     │  (ChromaDB)  │
└─────────────┘     └──────────────┘
```

---

## Benchmarks

Deterministic crawl throughput (fake scraper, no network):

| Graph | Workers | Pages | Time | Pages/sec |
|-------|---------|-------|------|-----------|
| Chain (depth 50) | 3 | 50 | 0.06s | **899** |
| Tree (branching 3, depth 3) | 5 | 40 | 0.05s | **737** |
| Star (hub + 100 leaves) | 5 | 101 | 0.06s | **1,671** |

Peak memory: **< 0.1 MB** for 100-page crawls.

Run your own: `python benchmarks/bench.py`

---

## CLI

```bash
huginn                          # Interactive mode with ASCII banner
huginn scrape <url> --format markdown
huginn crawl <url> --depth 3 --limit 50
huginn map <url> --limit 5000
huginn extract <url> --template product
huginn watch add <url> --webhook https://myapp.com/hook
huginn watch check <url>
huginn search <query>
huginn research "your research question"
huginn templates                # List all 10 extraction schemas
huginn serve --port 7432
huginn doctor                   # Check Playwright + system health
huginn config                   # Show current config
```

---

## API Endpoints

### Core

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/probe` | POST | Scrape a single URL |
| `/v1/sweep` | POST | Start async crawl (SSE / NDJSON) |
| `/v1/sweep/{id}` | GET | Get crawl status |
| `/v1/chart` | POST | Map site URLs (sitemap) |
| `/v1/graph` | POST | BFS site graph (nodes + edges) |
| `/v1/flock` | POST | Batch URL processing |

### Intelligence

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/distill` | POST | Structured LLM extraction with templates |
| `/v1/seek` | POST | Web search |
| `/v1/research` | POST | Deep multi-hop research with memory |

### Templates & Memory

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/templates` | GET | List all extraction templates |
| `/v1/templates/{name}` | GET | Single template schema + field guides |
| `/v1/memory/query` | GET | Semantic search over research memory |
| `/v1/memory/reports` | GET | List all research reports |
| `/v1/memory/reports/{id}` | DELETE | Delete a report |
| `/v1/memory/related` | GET | Find related topics |

### Watch & Schedule

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/watch` | POST | Start page change detection |
| `/v1/watch/{url}` | GET | Watch status |
| `/v1/watch/{url}/check` | POST | Manual check |
| `/v1/watch/{url}` | DELETE | Stop watching |
| `/v1/schedule` | POST | Cron/interval scheduling |

---

## Templates

10 built-in extraction schemas with JSON schema, field guides, and merge strategy:

| Template | Use case |
|----------|----------|
| `product` | E-commerce listings — price, availability, specs |
| `article` | News/blog posts — title, author, publish date, summary |
| `job_posting` | Careers pages — role, company, salary, requirements |
| `real_estate` | Property listings — price, beds, baths, location |
| `person` | Profiles — name, title, company, links |
| `event` | Event pages — date, venue, tickets, lineup |
| `review` | Review aggregators — rating, text, reviewer, date |
| `faq` | FAQ pages — question, answer, category |
| `recipe` | Food sites — ingredients, steps, time, nutrition |
| `research_paper` | Academic PDFs — title, authors, abstract, citations |

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

## Environment

```bash
export HUGINN_API_KEY="your-key"
export HUGINN_PORT=7432
export HUGINN_LOG_LEVEL=INFO
export HUGINN_BROWSER_HEADLESS=true
export HUGINN_BROWSER_STEALTH=true
export HUGINN_DATA_DIR="~/.huginn"       # For research memory / vector DB
```

---

## License

MIT — see [LICENSE](LICENSE)

---

Built with ❤️ by [Phnix](https://github.com/Null-Phnix)
