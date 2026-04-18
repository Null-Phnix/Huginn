"""
BlackCrawl CLI — Command line interface.
"""

import argparse
import asyncio
import json
import logging
import sys
from typing import Optional

from .config import BlackCrawlConfig, load_config


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="blackcrawl",
        description="BlackCrawl — Autonomous web scraping API",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # serve
    serve_parser = subparsers.add_parser("serve", help="Start the API server")
    serve_parser.add_argument("--host", default=None, help="Host to bind to")
    serve_parser.add_argument("--port", type=int, default=None, help="Port to bind to")
    serve_parser.add_argument("--config", default=None, help="Path to config file")
    serve_parser.add_argument("--no-stealth", action="store_true", help="Disable stealth mode")
    serve_parser.add_argument("--no-headless", action="store_true", help="Show browser UI")
    serve_parser.add_argument("--api-key", default=None, help="API key for auth")
    serve_parser.add_argument("--log-level", default=None, choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    # scrape
    scrape_parser = subparsers.add_parser("scrape", help="Scrape a single URL")
    scrape_parser.add_argument("url", help="URL to scrape")
    scrape_parser.add_argument("--format", "-f", default="markdown", choices=["markdown", "html", "rawHtml", "links"])
    scrape_parser.add_argument("--output", "-o", default=None, help="Output file (default: stdout)")
    scrape_parser.add_argument("--timeout", type=int, default=30000)

    # crawl
    crawl_parser = subparsers.add_parser("crawl", help="Crawl a site")
    crawl_parser.add_argument("url", help="Starting URL")
    crawl_parser.add_argument("--depth", type=int, default=3)
    crawl_parser.add_argument("--limit", type=int, default=100)
    crawl_parser.add_argument("--output", "-o", default=None, help="Output file")
    crawl_parser.add_argument("--concurrency", type=int, default=5)

    # map
    map_parser = subparsers.add_parser("map", help="Map site URLs")
    map_parser.add_argument("url", help="URL to map")
    map_parser.add_argument("--search", default=None, help="Filter URLs containing this")
    map_parser.add_argument("--limit", type=int, default=5000)
    map_parser.add_argument("--output", "-o", default=None)

    # search
    search_parser = subparsers.add_parser("search", help="Web search")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--limit", type=int, default=5)
    search_parser.add_argument("--output", "-o", default=None)

    # doctor
    subparsers.add_parser("doctor", help="Check system health")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "serve":
        _cmd_serve(args)
    elif args.command == "scrape":
        asyncio.run(_cmd_scrape(args))
    elif args.command == "crawl":
        asyncio.run(_cmd_crawl(args))
    elif args.command == "map":
        asyncio.run(_cmd_map(args))
    elif args.command == "search":
        asyncio.run(_cmd_search(args))
    elif args.command == "doctor":
        asyncio.run(_cmd_doctor(args))


def _cmd_serve(args):
    """Start the API server."""
    config = load_config(args.config)

    if args.host:
        config.server.host = args.host
    if args.port:
        config.server.port = args.port
    if args.no_stealth:
        config.browser.stealth_mode = False
    if args.no_headless:
        config.browser.headless = False
    if args.api_key:
        config.server.api_key = args.api_key
    if args.log_level:
        config.log_level = args.log_level

    import uvicorn
    from .api import create_app

    app = create_app(config)
    uvicorn.run(app, host=config.server.host, port=config.server.port, log_level=config.log_level.lower())


async def _cmd_scrape(args):
    """CLI scrape command."""
    from .browser import BrowserManager
    from .scraper import Scraper
    from .models import OutputFormat

    browser = BrowserManager(headless=True, stealth=True)
    try:
        await browser.start()
        scraper = Scraper(browser)

        fmt_map = {
            "markdown": OutputFormat.MARKDOWN,
            "html": OutputFormat.HTML,
            "rawHtml": OutputFormat.RAW_HTML,
            "links": OutputFormat.LINKS,
        }

        data = await scraper.scrape(
            url=args.url,
            formats=[fmt_map[args.format]],
            timeout=args.timeout,
        )

        result = data.model_dump(by_alias=True, exclude_none=True)
        output = json.dumps(result, indent=2, ensure_ascii=False)

        if args.output:
            with open(args.output, "w") as f:
                f.write(output)
            print(f"Written to {args.output}")
        else:
            print(output)

    finally:
        await browser.stop()


async def _cmd_crawl(args):
    """CLI crawl command."""
    from .browser import BrowserManager
    from .crawler import Crawler
    from .models import OutputFormat

    browser = BrowserManager(headless=True, stealth=True)
    try:
        await browser.start()
        crawler = Crawler(
            browser=browser,
            max_depth=args.depth,
            max_pages=args.limit,
            concurrency=args.concurrency,
        )

        result = await crawler.crawl(
            start_url=args.url,
            scrape_formats=[OutputFormat.MARKDOWN],
            on_progress=lambda c, t: print(f"\r  Crawled: {c}/{t or '?'}", end="", flush=True),
        )

        print()  # newline after progress

        output_data = {
            "pages": [{"url": p.metadata.get("url", "") if p.metadata else "",
                       "markdown": p.markdown} for p in result.pages if p.markdown],
            "stats": {
                "crawled": result.completed,
                "discovered": result.total_discovered,
                "errors": len(result.errors),
                "elapsed": round(result.elapsed, 1),
            }
        }

        output = json.dumps(output_data, indent=2, ensure_ascii=False)
        if args.output:
            with open(args.output, "w") as f:
                f.write(output)
            print(f"Written to {args.output}")
        else:
            print(output)

    finally:
        await browser.stop()


async def _cmd_map(args):
    """CLI map command."""
    from .browser import BrowserManager
    from .mapper import Mapper

    browser = BrowserManager(headless=True, stealth=True)
    try:
        await browser.start()
        mapper = Mapper(browser)

        links = await mapper.map_site(
            url=args.url,
            search=args.search,
            limit=args.limit,
        )

        output = json.dumps({"links": links, "count": len(links)}, indent=2)
        if args.output:
            with open(args.output, "w") as f:
                f.write(output)
            print(f"Found {len(links)} URLs. Written to {args.output}")
        else:
            print(output)

    finally:
        await browser.stop()


async def _cmd_search(args):
    """CLI search command."""
    from .browser import BrowserManager
    from .searcher import Searcher
    from .models import OutputFormat

    browser = BrowserManager(headless=True, stealth=True)
    try:
        await browser.start()
        searcher = Searcher(browser)

        results = await searcher.search(
            query=args.query,
            limit=args.limit,
            scrape_formats=[OutputFormat.MARKDOWN],
        )

        output_data = []
        for item in results:
            entry = {}
            if item.metadata:
                entry["title"] = item.metadata.get("title", "")
                entry["url"] = item.metadata.get("url", "")
                entry["snippet"] = item.metadata.get("snippet", "")
            if item.markdown:
                entry["content"] = item.markdown[:5000]
            output_data.append(entry)

        output = json.dumps(output_data, indent=2, ensure_ascii=False)
        if args.output:
            with open(args.output, "w") as f:
                f.write(output)
            print(f"Found {len(results)} results. Written to {args.output}")
        else:
            print(output)

    finally:
        await browser.stop()


async def _cmd_doctor(args):
    """Check system health."""
    print("BlackCrawl Doctor")
    print("=" * 40)

    # Check Python version
    import sys
    print(f"  Python: {sys.version.split()[0]}")

    # Check dependencies
    deps = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "playwright": "playwright",
        "httpx": "httpx",
        "aiosqlite": "aiosqlite",
        "pydantic": "pydantic",
    }
    for name, pkg in deps.items():
        try:
            mod = __import__(pkg)
            ver = getattr(mod, "__version__", "installed")
            print(f"  {name}: {ver}")
        except ImportError:
            print(f"  {name}: MISSING")

    # Check Playwright browsers
    pw = None
    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("about:blank")
        await browser.close()
        print("  Playwright Chromium: OK")
    except Exception as e:
        print(f"  Playwright Chromium: FAILED ({e})")
    finally:
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass

    # Check config
    config = load_config()
    print(f"  Data dir: {config.data_dir}")
    print(f"  DB path: {config.db_path}")
    print(f"  Default port: {config.server.port}")

    print("\nAll checks complete.")


if __name__ == "__main__":
    main()