"""
Huginn CLI — Rich interactive command-line interface.

Usage:
    huginn                          # Interactive mode (default)
    huginn scrape <url>             # Scrape a single URL
    huginn extract <urls>...        # Extract structured data
    huginn crawl <url>              # Crawl a site recursively
    huginn search "<query>"         # Search the web
    huginn map <url>                # Map site URLs
    huginn research "<query>"       # Deep research
    huginn serve                    # Start the API server
    huginn doctor                   # Check system health
    huginn templates                # List extraction templates
    huginn config                   # Show current config
"""

import asyncio
import csv
import json
import os
import platform
import sys
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any, List, Optional

import click
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax
from rich import box

from huginn import __version__

console = Console()


def _banner():
    v = __version__
    return f"""[bold bright_cyan]
╔══════════════════════════════════════════════════════════╗
║  Huginn  —  Autonomous Web Scraping API                  ║
║                                                          ║
║          v{v:<10}                     ║
╚══════════════════════════════════════════════════════════╝[/bold bright_cyan]"""


def _print_banner():
    console.print(_banner())


def _write_output(data, path: str, fmt: str):
    """Write data to file or stdout in requested format."""
    import csv, json, yaml
    text = ""
    if fmt == "json":
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    elif fmt == "yaml":
        text = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    elif fmt == "csv":
        if isinstance(data, list) and data and isinstance(data[0], dict):
            buf = StringIO(); w = csv.DictWriter(buf, fieldnames=data[0].keys()); w.writeheader(); w.writerows(data)
            text = buf.getvalue()
        else:
            raise click.BadParameter("CSV requires a list of flat dicts")
    elif fmt == "markdown":
        text = _to_markdown(data)
    elif fmt == "raw":
        text = str(data)
    else:
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if path and path != "-":
        Path(path).write_text(text, encoding="utf-8")
        console.print(f"[green]Wrote {Path(path).resolve()}[/green]")
    else:
        if fmt in ("json", "yaml"):
            console.print(Syntax(text, fmt, theme="monokai"))
        else:
            console.print(text)


def _to_markdown(data):
    if isinstance(data, dict):
        lines = ["# Results", ""]
        for k, v in data.items():
            lines.append(f"## {k}")
            if isinstance(v, (list, dict)):
                lines.append("```json"); lines.append(json.dumps(v, indent=2, ensure_ascii=False)); lines.append("```")
            else:
                lines.append(str(v))
            lines.append("")
        return "\n".join(lines)
    elif isinstance(data, list):
        lines = ["# Results", ""]
        for i, item in enumerate(data, 1):
            lines.append(f"## Item {i}")
            if isinstance(item, dict):
                for k, v in item.items(): lines.append(f"- **{k}**: {v}")
            else:
                lines.append(str(item))
            lines.append("")
        return "\n".join(lines)
    return str(data)


def _load_urls(source: str) -> list:
    """Load URLs from file or stdin."""
    raw = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    return [l.strip() for l in raw.strip().splitlines() if l.strip() and not l.strip().startswith("#")]


def _run_async(coro):
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Interrupted.[/bold yellow]")
        return None


async def _get_browser(headless: bool = True, stealth: bool = True):
    from .browser import BrowserManager
    browser = BrowserManager(headless=headless, stealth=stealth)
    await browser.start()
    return browser


async def _stop_browser(browser):
    try:
        await browser.stop()
    except Exception:
        pass


# ─── Interactive Mode ────────────────────────────────────────────────────

def _menu():
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold cyan", width=8)
    table.add_column("Command", style="bold white", width=20)
    table.add_column("Description", style="dim")
    items = [
        ("[s]", "scrape",   "Probe a single URL"),
        ("[c]", "crawl",    "Sweep a site recursively"),
        ("[e]", "extract",  "Extract structured data"),
        ("[b]", "batch",    "Batch process URLs from file"),
        ("[r]", "search",   "Seek the web"),
        ("[m]", "map",      "Map site URLs"),
        ("[d]", "research", "Deep research"),
        ("[v]", "serve",    "Start API server"),
        ("[t]", "templates","List extract templates"),
        ("[M]", "memory",   "Query research memory"),
        ("[W]", "watch",    "Manage page watches"),
        ("[j]", "jobs",     "List running jobs"),
        ("[o]", "config",   "Show config"),
        ("[h]", "doctor",   "Health check"),
        ("[q]", "quit",     "Exit"),
    ]
    for key, cmd, desc in items:
        table.add_row(key, cmd, desc)
    console.print()
    console.print(Panel(table, title="[bold]Menu[/bold]", border_style="cyan"))
    console.print()


async def _i_scrape():
    url = Prompt.ask("[bold]URL to scrape[/bold]", console=console)
    if not url.strip():
        console.print("[red]URL required.[/red]"); return
    fmt = Prompt.ask("Format", choices=["markdown", "html", "links"],
                     default="markdown", console=console)
    out = Prompt.ask("Output file (empty for stdout)", default="", console=console)
    await _do_scrape(url, fmt, out)


async def _do_scrape(url: str, fmt: str, out: str, outfmt: str = "json"):
    from .scraper import Scraper
    from .models import OutputFormat
    fmt_map = {"markdown": OutputFormat.MARKDOWN, "html": OutputFormat.HTML,
               "links": OutputFormat.LINKS}
    browser = None
    try:
        with Progress(SpinnerColumn(), TextColumn("[bold cyan]Scraping..."),
                      console=console) as p:
            p.add_task("scrape")
            browser = await _get_browser()
            scraper = Scraper(browser)
            data = await scraper.scrape(url=url, formats=[fmt_map[fmt]])
        _write_output(data.model_dump(exclude_none=True), out, outfmt)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        if browser:
            await _stop_browser(browser)


async def _i_extract():
    url = Prompt.ask("[bold]URL to extract from[/bold]", console=console)
    if not url.strip():
        console.print("[red]URL required.[/red]"); return
    tpl_name = Prompt.ask("Template (empty for none)", default="", console=console)
    tpl = None
    if tpl_name.strip():
        from .templates import get_template
        try:
            tpl = get_template(tpl_name.strip())
            console.print(f"[green]Using template: {tpl_name.strip()}[/green]")
        except KeyError as e:
            console.print(f"[red]{e}[/red]"); return
    out = Prompt.ask("Output file (empty for stdout)", default="", console=console)
    await _do_extract([url], tpl, out)


async def _do_extract(urls, tpl, out: str):
    from .extractor import Extractor
    from .templates import ExtractTemplate
    browser = None
    try:
        with Progress(SpinnerColumn(), TextColumn("[bold cyan]Extracting..."),
                      console=console) as p:
            p.add_task("extract")
            browser = await _get_browser()
            ext = Extractor(browser, max_retries=3, mental_model=True)
            t = tpl if isinstance(tpl, ExtractTemplate) else None
            result = await ext.extract(urls=urls, template=t, include_raw=True)
        result = json.dumps(result, indent=2, ensure_ascii=False)
        if out.strip():
            Path(out).write_text(result, encoding="utf-8")
            console.print(f"[green]Wrote {out}[/green]")
        else:
            console.print(Syntax(result, "json", theme="monokai"))
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        if browser:
            await _stop_browser(browser)


async def _i_crawl():
    url = Prompt.ask("[bold]Starting URL[/bold]", console=console)
    if not url.strip():
        console.print("[red]URL required.[/red]"); return
    depth = int(Prompt.ask("Max depth", default="2", console=console))
    limit = int(Prompt.ask("Page limit", default="50", console=console))
    out = Prompt.ask("Output file (empty for stdout)", default="", console=console)
    await _do_crawl(url, depth, limit, out)


async def _do_crawl(url, depth, limit, out):
    from .crawler import Crawler
    from .models import OutputFormat
    browser = None
    try:
        browser = await _get_browser()
        crawler = Crawler(browser=browser, max_depth=depth, max_pages=limit)
        with Progress(SpinnerColumn(), TextColumn("[bold cyan]Crawling..."),
                      console=console) as p:
            p.add_task("crawl")
            result = await crawler.crawl(start_url=url,
                scrape_formats=[OutputFormat.MARKDOWN], on_progress=lambda c, t: None)
        data = {
            "pages": [{"url": pg.metadata.get("url","") if pg.metadata else "",
                       "markdown": pg.markdown} for pg in result.pages if pg.markdown],
            "stats": {
                "crawled": result.completed,
                "discovered": result.total_discovered,
                "errors": len(result.errors),
                "elapsed": round(result.elapsed, 1),
            },
        }
        result = json.dumps(data, indent=2, ensure_ascii=False)
        if out.strip():
            Path(out).write_text(result, encoding="utf-8")
            console.print(f"[green]Wrote {out}[/green]")
        else:
            console.print(Syntax(result, "json", theme="monokai"))
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        if browser:
            await _stop_browser(browser)


async def _i_search():
    q = Prompt.ask("[bold]Search query[/bold]", console=console)
    if not q.strip():
        console.print("[red]Query required.[/red]"); return
    limit = int(Prompt.ask("Max results", default="5", console=console))
    out = Prompt.ask("Output file (empty for stdout)", default="", console=console)
    await _do_search(q, limit, out)


async def _do_search(q, limit, out):
    from .searcher import Searcher
    from .models import OutputFormat
    browser = None
    try:
        with Progress(SpinnerColumn(), TextColumn("[bold cyan]Searching..."),
                      console=console) as p:
            p.add_task("search")
            browser = await _get_browser()
            searcher = Searcher(browser)
            results = await searcher.search(query=q, limit=limit,
                scrape_formats=[OutputFormat.MARKDOWN])
        data = []
        for item in results:
            entry = {}
            if item.metadata:
                entry.update({"title": item.metadata.get("title",""),
                              "url": item.metadata.get("url",""),
                              "snippet": item.metadata.get("snippet","")})
            if item.markdown:
                entry["content"] = item.markdown[:5000]
            data.append(entry)
        result = json.dumps(data, indent=2, ensure_ascii=False)
        if out.strip():
            Path(out).write_text(result, encoding="utf-8")
            console.print(f"[green]Wrote {out}[/green]")
        else:
            console.print(Syntax(result, "json", theme="monokai"))
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        if browser:
            await _stop_browser(browser)


async def _i_map():
    url = Prompt.ask("[bold]URL to map[/bold]", console=console)
    if not url.strip():
        console.print("[red]URL required.[/red]"); return
    search = Prompt.ask("Filter (empty for all)", default="", console=console)
    limit = int(Prompt.ask("Limit", default="5000", console=console))
    out = Prompt.ask("Output file (empty for stdout)", default="", console=console)
    await _do_map(url, search or None, limit, out)


async def _do_map(url, search, limit, out):
    from .mapper import Mapper
    browser = None
    try:
        with Progress(SpinnerColumn(), TextColumn("[bold cyan]Mapping..."),
                      console=console) as p:
            p.add_task("map")
            browser = await _get_browser()
            mapper = Mapper(browser)
            links = await mapper.map_site(url=url, search=search, limit=limit)
        data = {"links": links, "count": len(links)}
        result = json.dumps(data, indent=2, ensure_ascii=False)
        if out.strip():
            Path(out).write_text(result, encoding="utf-8")
            console.print(f"[green]Wrote {out} ({len(links)} URLs)[/green]")
        else:
            console.print(Syntax(result, "json", theme="monokai"))
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        if browser:
            await _stop_browser(browser)


async def _i_research():
    q = Prompt.ask("[bold]Research topic[/bold]", console=console)
    if not q.strip():
        console.print("[red]Query required.[/red]"); return
    depth = int(Prompt.ask("Research depth (1-5)", default="3", console=console))
    max_src = int(Prompt.ask("Max sources", default="10", console=console))
    out = Prompt.ask("Output file (empty for stdout)", default="", console=console)
    await _do_research(q, depth, max_src, out)


async def _do_research(q, depth, max_src, out: str, output_format: str = "json"):
    from .researcher import DeepResearcher
    from .memory import ResearchMemory
    browser = None
    try:
        browser = await _get_browser()
        researcher = DeepResearcher(browser=browser, llm_provider="openai")
        with Progress(SpinnerColumn(), TextColumn("[bold cyan]Researching..."),
                      console=console) as p:
            p.add_task("research")
            report = await researcher.research(query=q, depth=depth,
                max_sources=max_src, target_length="standard")
        result = report.to_dict()
        _write_output(result, out, output_format)
        memory = ResearchMemory()
        if memory.available:
            await memory.add_report(report)
            console.print("[dim]Persisted to research memory.[/dim]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        if browser:
            await _stop_browser(browser)




async def _do_batch(source: str, operation: str, tpl_name, out: str, output_format: str, concurrency: int):
    """Batch process URLs from file/stdin."""
    urls = _load_urls(source)
    if not urls:
        console.print("[red]No URLs found.[/red]"); return
    console.print(f"[dim]Loaded {len(urls)} URLs[/dim]")
    tpl = None
    if tpl_name:
        from .templates import get_template
        tpl = get_template(tpl_name)
    browser = None
    try:
        browser = await _get_browser()
        all_results = []
        with Progress(SpinnerColumn(), TextColumn("[bold cyan]{task.description}"),
                      console=console) as p:
            task = p.add_task(f"Batch {operation}...", total=len(urls))
            for i, url in enumerate(urls):
                p.update(task, description=f"{operation}: {url[:60]}")
                try:
                    if operation == "scrape":
                        from .scraper import Scraper
                        from .models import OutputFormat
                        scraper = Scraper(browser)
                        data = await scraper.scrape(url=url, formats=[OutputFormat.MARKDOWN])
                        all_results.append({"url": url, "status": "ok",
                                            "title": data.metadata.get("title","") if data.metadata else "",
                                            "markdown": data.markdown[:2000]})
                    elif operation == "extract":
                        from .extractor import Extractor
                        ext = Extractor(browser, max_retries=2, mental_model=False)
                        t = tpl if tpl else None
                        result = await ext.extract(urls=[url], template=t, include_raw=False)
                        all_results.append({"url": url, "status": "ok", "data": result})
                    elif operation == "map":
                        from .mapper import Mapper
                        mapper = Mapper(browser)
                        links = await mapper.map_site(url=url, limit=500)
                        all_results.append({"url": url, "status": "ok", "links": links})
                except Exception as e:
                    all_results.append({"url": url, "status": "error", "error": str(e)})
                p.update(task, advance=1)
        _write_output(all_results, out, output_format)
    except Exception as e:
        console.print(f"[red]Batch error: {e}[/red]")
    finally:
        if browser:
            await _stop_browser(browser)


async def _memory_query(q, n, out, outfmt):
    from .memory import ResearchMemory
    memory = ResearchMemory()
    if not memory.available:
        console.print("[red]Research memory not available[/red]"); return
    with Progress(SpinnerColumn(), TextColumn("[bold cyan]Querying memory..."), console=console) as p:
        p.add_task("query")
        results = await memory.query(query_text=q, n_results=n)
    _write_output(results, out, outfmt)


async def _memory_reports(out, outfmt):
    from .memory import ResearchMemory
    memory = ResearchMemory()
    if not memory.available:
        console.print("[red]Research memory not available[/red]"); return
    reports = await memory.get_all_reports()
    _write_output({"reports": reports, "count": len(reports)}, out, outfmt)


async def _memory_related(topic, n, out, outfmt):
    from .memory import ResearchMemory
    memory = ResearchMemory()
    if not memory.available:
        console.print("[red]Research memory not available[/red]"); return
    topics = await memory.get_related_topics(topic, n_results=n)
    _write_output({"topic": topic, "related": topics, "count": len(topics)}, out, outfmt)


async def _memory_delete(report_id):
    from .memory import ResearchMemory
    memory = ResearchMemory()
    if not memory.available:
        console.print("[red]Research memory not available[/red]"); return
    await memory.delete_report(report_id)
    console.print(f"[green]Deleted report {report_id}[/green]")


async def _watch_add(url, selector, interval, webhook):
    from .watcher import PageWatcher
    watcher = PageWatcher()
    entry = await watcher.add_watch(url=url, selector=selector, interval=interval, webhook_url=webhook)
    console.print(f"[green]Added watch for {url}[/green]")
    console.print(f"[dim]Watch ID: {entry.get('id', 'n/a')} | Interval: {interval}s[/dim]")


async def _watch_list(out, outfmt):
    from .watcher import PageWatcher
    watcher = PageWatcher()
    watches = await watcher.list_watches()
    _write_output({"watches": watches, "count": len(watches)}, out, outfmt)


async def _watch_check(url):
    from .watcher import PageWatcher
    watcher = PageWatcher()
    result = await watcher.check_now(url)
    if result.get("changed"):
        console.print(f"[yellow]CHANGED:[/yellow] {url}")
        console.print(result.get("diff", "")[:500])
    else:
        console.print(f"[green]No change:[/green] {url}")


async def _watch_remove(url):
    from .watcher import PageWatcher
    watcher = PageWatcher()
    await watcher.remove_watch(url)
    console.print(f"[green]Removed watch for {url}[/green]")


async def _jobs_list(out, outfmt):
    from .job_store import JobStore
    from .config import load_config
    cfg = load_config()
    store = JobStore(cfg.db_path)
    await store.init()
    jobs = await store.list_jobs()
    for j in jobs:
        ts = j.get("created_at")
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                age = datetime.now(dt.tzinfo or None) - dt
                j["age"] = str(age).split(".")[0]
            except Exception:
                j["age"] = "unknown"
    _write_output({"jobs": jobs, "count": len(jobs)}, out, outfmt)

# ─── Click CLI ─────────────────────────────────────────────────────────────

@click.group(invoke_without_command=True, context_settings=dict(help_option_names=["-h", "--help"]))
@click.version_option(version=__version__, prog_name="Huginn")
@click.pass_context
def cli(ctx):
    """Huginn — Autonomous web scraping API.\n\n
    Run 'huginn COMMAND --help' for details on any subcommand.
    """
    if ctx.invoked_subcommand is None:
        # No command = interactive mode (but check if stdin is a tty)
        if sys.stdin.isatty():
            _interactive_mode()
        else:
            ctx.get_help()
            ctx.exit()


# ─── scrape ────────────────────────────────────────────────────────────────

@cli.command(name="scrape")
@click.argument("url")
@click.option("-f", "--format", "fmt", type=click.Choice(["markdown", "html", "links"]),
              default="markdown", help="Output format")
@click.option("-o", "--output", default="-", help="Output file (default: stdout)")
@click.option("-F", "--out-format", "outfmt", type=click.Choice(["json", "yaml", "csv", "markdown", "raw"]),
              default="json", help="Serialization format")
def scrape_cmd(url, fmt, output, outfmt):
    """Scrape a single URL."""
    _run_async(_do_scrape(url, fmt, output, outfmt))


# ─── extract ───────────────────────────────────────────────────────────────

@cli.command(name="extract")
@click.argument("urls", nargs=-1, required=True)
@click.option("-t", "--template", default=None, help="Template name (product, article, ...)")
@click.option("-o", "--output", default="-", help="Output file")
@click.option("-F", "--out-format", "outfmt", type=click.Choice(["json", "yaml", "csv", "markdown", "raw"]),
              default="json", help="Serialization format")
def extract_cmd(urls, template, output, outfmt):
    """Extract structured data from URLs."""
    tpl = None
    if template:
        from .templates import get_template
        tpl = get_template(template)
    _run_async(_do_extract(list(urls), tpl, output, outfmt))


# ─── crawl ───────────────────────────────────────────────────────────────────

@cli.command(name="crawl")
@click.argument("url")
@click.option("-d", "--depth", default=2, show_default=True, help="Max crawl depth")
@click.option("-l", "--limit", default=50, show_default=True, help="Max pages")
@click.option("-o", "--output", default="-", help="Output file")
@click.option("-F", "--out-format", "outfmt", type=click.Choice(["json", "yaml", "csv", "markdown", "raw"]),
              default="json", help="Serialization format")
def crawl_cmd(url, depth, limit, output, outfmt):
    """Crawl a site recursively."""
    _run_async(_do_crawl(url, depth, limit, output, outfmt))


# ─── search ──────────────────────────────────────────────────────────────────

@cli.command(name="search")
@click.argument("query")
@click.option("-l", "--limit", default=5, show_default=True, help="Max results")
@click.option("-o", "--output", default="-", help="Output file")
@click.option("-F", "--out-format", "outfmt", type=click.Choice(["json", "yaml", "csv", "markdown", "raw"]),
              default="json", help="Serialization format")
def search_cmd(query, limit, output, outfmt):
    """Search the web."""
    _run_async(_do_search(query, limit, output, outfmt))


# ─── map ─────────────────────────────────────────────────────────────────────

@cli.command(name="map")
@click.argument("url")
@click.option("-l", "--limit", default=5000, show_default=True, help="Max URLs")
@click.option("-s", "--search-filter", default=None, help="Filter URLs")
@click.option("-o", "--output", default="-", help="Output file")
@click.option("-F", "--out-format", "outfmt", type=click.Choice(["json", "yaml", "csv", "markdown", "raw"]),
              default="json", help="Serialization format")
def map_cmd(url, limit, search_filter, output, outfmt):
    """Map/discover URLs on a site."""
    _run_async(_do_map(url, search_filter, limit, output, outfmt))


# ─── research ────────────────────────────────────────────────────────────────

@cli.command(name="research")
@click.argument("query")
@click.option("-d", "--depth", default=3, show_default=True, help="Research depth 1-5")
@click.option("-s", "--sources", default=10, show_default=True, help="Max sources")
@click.option("-o", "--output", default="-", help="Output file")
@click.option("-F", "--out-format", "outfmt", type=click.Choice(["json", "yaml", "csv", "markdown", "raw"]),
              default="json", help="Serialization format")
def research_cmd(query, depth, sources, output, outfmt):
    """Run deep autonomous research."""
    _run_async(_do_research(query, depth, sources, output, outfmt))


# ─── serve ──────────────────────────────────────────────────────────────────────

@cli.command(name="serve")
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=7432, show_default=True)
@click.option("--config", default=None, help="Config file path")
@click.option("--no-stealth/--stealth", default=False, help="Disable stealth modes")
@click.option("--no-headless/--headless", default=False, help="Show browser window")
@click.option("--api-key", default=None, help="API key override")
@click.option("--log-level", default="INFO", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]),
              show_default=True)
def serve_cmd(host, port, config, no_stealth, no_headless, api_key, log_level):
    """Start the API server."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    from .config import load_config
    from .api import create_app
    import uvicorn

    cfg = load_config(config)
    if host:
        cfg.server.host = host
    if port:
        cfg.server.port = port
    if no_stealth:
        cfg.browser.stealth_mode = False
    if no_headless:
        cfg.browser.headless = False
    if api_key:
        cfg.server.api_key = api_key
    if log_level:
        cfg.log_level = log_level

    console.print(f"[green]Starting server on {cfg.server.host}:{cfg.server.port}[/green]")
    uvicorn.run(
        create_app(cfg),
        host=cfg.server.host,
        port=cfg.server.port,
        log_level=cfg.log_level.lower(),
    )


# ─── templates ─────────────────────────────────────────────────────────────────

@cli.command(name="templates")
def templates_cmd():
    """List available extraction templates."""
    from .templates import get_all_templates
    table = Table(title="[bold]Extraction Templates[/bold]", border_style="cyan")
    table.add_column("Name", style="bold cyan")
    table.add_column("Description", style="dim")
    table.add_column("Required Fields", style="yellow")
    for name, t in get_all_templates().items():
        req = ", ".join(t.schema.get("required", [""]))
        table.add_row(name, t.description, req)
    console.print(table)



# ─── batch ───────────────────────────────────────────────────────────────────

@cli.command(name="batch")
@click.argument("source")
@click.option("-x", "--operation", type=click.Choice(["scrape", "extract", "map"]),
              default="scrape", help="Operation to run on each URL")
@click.option("-t", "--template", default=None, help="Template for extract operation")
@click.option("-o", "--output", default="-", help="Output file")
@click.option("-F", "--out-format", "outfmt", type=click.Choice(["json", "yaml", "csv", "markdown", "raw"]),
              default="json", help="Serialization format")
@click.option("-c", "--concurrency", default=1, help="Max concurrent (future)")
def batch_cmd(source, operation, template, output, outfmt, concurrency):
    """Batch process URLs from a file or stdin (use '-' for stdin)."""
    _run_async(_do_batch(source, operation, template, output, outfmt, concurrency))


# ─── memory ──────────────────────────────────────────────────────────────────

@cli.group(name="memory")
def memory_group():
    """Manage research memory."""

@memory_group.command(name="query")
@click.argument("query_text")
@click.option("-n", default=10, show_default=True, help="Max results")
@click.option("-o", "--output", default="-", help="Output file")
@click.option("-F", "--out-format", "outfmt", type=click.Choice(["json", "yaml", "csv", "markdown", "raw"]),
              default="json")
def memory_query_cmd(query_text, n, output, outfmt):
    """Semantic search over accumulated research memory."""
    _run_async(_memory_query(query_text, n, output, outfmt))

@memory_group.command(name="reports")
@click.option("-o", "--output", default="-", help="Output file")
@click.option("-F", "--out-format", "outfmt", type=click.Choice(["json", "yaml", "csv", "markdown", "raw"]),
              default="json")
def memory_reports_cmd(output, outfmt):
    """List all stored research reports."""
    _run_async(_memory_reports(output, outfmt))

@memory_group.command(name="related")
@click.argument("topic")
@click.option("-n", default=10, show_default=True)
@click.option("-o", "--output", default="-", help="Output file")
@click.option("-F", "--out-format", "outfmt", type=click.Choice(["json", "yaml", "csv", "markdown", "raw"]),
              default="json")
def memory_related_cmd(topic, n, output, outfmt):
    """Find topics related to a given topic."""
    _run_async(_memory_related(topic, n, output, outfmt))

@memory_group.command(name="delete")
@click.argument("report_id")
def memory_delete_cmd(report_id):
    """Delete a research report from memory."""
    _run_async(_memory_delete(report_id))


# ─── watch ────────────────────────────────────────────────────────────────────

@cli.group(name="watch")
def watch_group():
    """Manage page watches / change detection."""

@watch_group.command(name="add")
@click.argument("url")
@click.option("--selector", default=None, help="CSS selector to watch")
@click.option("--interval", default=3600, show_default=True, help="Check interval in seconds")
@click.option("--webhook", default=None, help="Webhook URL for notifications")
def watch_add_cmd(url, selector, interval, webhook):
    """Add a page watch."""
    _run_async(_watch_add(url, selector, interval, webhook))

@watch_group.command(name="list")
@click.option("-o", "--output", default="-", help="Output file")
@click.option("-F", "--out-format", "outfmt", type=click.Choice(["json", "yaml", "csv", "markdown", "raw"]),
              default="json")
def watch_list_cmd(output, outfmt):
    """List all active watches."""
    _run_async(_watch_list(output, outfmt))

@watch_group.command(name="check")
@click.argument("url")
def watch_check_cmd(url):
    """Check a watched URL for changes now."""
    _run_async(_watch_check(url))

@watch_group.command(name="remove")
@click.argument("url")
def watch_remove_cmd(url):
    """Remove a page watch."""
    _run_async(_watch_remove(url))


# ─── jobs ────────────────────────────────────────────────────────────────────

@cli.command(name="jobs")
@click.option("-o", "--output", default="-", help="Output file")
@click.option("-F", "--out-format", "outfmt", type=click.Choice(["json", "yaml", "csv", "markdown", "raw"]),
              default="json")
def jobs_cmd(output, outfmt):
    """List running and queued jobs."""
    _run_async(_jobs_list(output, outfmt))


# ─── completion ───────────────────────────────────────────────────────────────

@cli.command(name="completion")
@click.option("--shell", type=click.Choice(["bash", "zsh", "fish"]),
              default="bash", help="Shell type")
def completion_cmd(shell):
    """Print shell completion script.

    Example: eval "$(huginn completion --shell zsh)"
    """
    prog = "huginn"
    if shell == "bash":
        script = f"""_huginn_completion(){{
    local IFS=$'\n'
    local response
    response=$(env COMP_WORDS="${{COMP_WORDS[*]}}" COMP_CWORD=$COMP_CWORD {prog} --completion show)
    for completion in $response; do
        IFS=',' read type value <<< "$completion"
        if [[ $type == 'dir' ]]; then COMREPLY+=("$value")
        elif [[ $type == 'file' ]]; then COMPREPLY+=("$value")
        else COMPREPLY+=("$value"); fi
    done
}}
complete -o nosort -F _huginn_completion {prog}"""
    elif shell == "zsh":
        script = f"""#compdef {prog}
_huginn_completion(){{
    local -a completions descriptions response
    local IFS=$'\n'
    response=(${{(ps:/:)"$(env COMP_WORDS="${{words[*]}}" COMP_CWORD=$((CURRENT-1)) {prog} --completion show)"}})
    for completion in $response; do
        IFS=',' read type value <<< "$completion"
        if [[ $type == 'dir' ]]; then _path_files -/
        elif [[ $type == 'file' ]]; then _path_files
        else completions+=($value); fi
    done
    if [ -n "$completions" ]; then _describe -V 'completions' completions; fi
}}
compdef _huginn_completion {prog}"""
    elif shell == "fish":
        script = f"""complete -c {prog} -f
function _{prog}_completion
    set -l response (env COMP_WORDS=(commandline -o) COMP_CWORD=(math (count (commandline -o)) - 1) {prog} --completion show)
    for completion in $response
        set -l parts (string split ',' $completion)
        if test $parts[1] = 'dir'; __fish_complete_directories
        else if test $parts[1] = 'file'; __fish_complete_path
        else; echo $parts[2]; end
    end
end
complete -c {prog} -a '(_{prog}_completion)'"""
    else:
        script = "# Shell completion not available\n"
    console.print(script)

# ─── config ──────────────────────────────────────────────────────────────────

@cli.command(name="config")
@click.option("-o", "--output", default="-", help="Output file")
@click.option("-F", "--out-format", "outfmt", type=click.Choice(["json", "yaml", "raw"]),
              default="json")
def config_cmd(output, outfmt):
    """Show current configuration."""
    from .config import load_config
    cfg = load_config()
    data = {
        "browser": {
            "backend": cfg.browser.backend,
            "headless": cfg.browser.headless,
            "viewport": f"{cfg.browser.viewport_width}x{cfg.browser.viewport_height}",
        },
        "crawl": {
            "max_depth": cfg.crawl.max_depth if hasattr(cfg, "crawl") else 3,
            "max_pages": cfg.crawl.max_pages if hasattr(cfg, "crawl") else 100,
        },
        "server": {
            "host": cfg.server.host,
            "port": cfg.server.port,
            "api_key": "***" if cfg.server.api_key else "none",
        },
        "data_dir": str(cfg.data_dir),
        "db_path": str(cfg.db_path),
    }
    _write_output(data, output, outfmt)


# ─── doctor ──────────────────────────────────────────────────────────────────

@cli.command(name="doctor")
def doctor_cmd():
    """Check system health."""
    _run_async(_doctor())


async def _doctor():
    table = Table(title="[bold]System Health[/bold]", border_style="green")
    table.add_column("Component", style="bold")
    table.add_column("Status", style="bold")
    table.add_column("Details", style="dim")
    table.add_row("Python", "[green]OK[/green]", platform.python_version())

    deps = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "playwright": "playwright",
        "httpx": "httpx",
        "pydantic": "pydantic",
        "chromadb": "chromadb",
        "rich": "rich",
        "click": "click",
    }
    pw_ok = False
    for name, pkg in deps.items():
        try:
            mod = __import__(pkg)
            ver = getattr(mod, "__version__", "installed")
            if name == "playwright":
                pw_ok = True
            table.add_row(name, "[green]OK[/green]", ver)
        except ImportError:
            table.add_row(name, "[red]MISSING[/red]", "")

    if pw_ok:
        try:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto("about:blank")
            await browser.close()
            await pw.stop()
            table.add_row("Chromium browser", "[green]OK[/green]", "Launched successfully")
        except Exception as e:
            table.add_row("Chromium browser", "[red]FAIL[/red]", str(e)[:50])
    else:
        table.add_row("Chromium browser", "[red]MISSING[/red]", "")

    from .config import load_config
    cfg = load_config()
    table.add_row("Data dir", "[green]OK[/green]", str(cfg.data_dir))
    table.add_row("DB path", "[green]OK[/green]", str(cfg.db_path))
    table.add_row("Default port", "[green]OK[/green]", str(cfg.server.port))
    console.print(table)


# ─── Interactive Mode Entry ──────────────────────────────────────────────────

def _interactive_mode():
    _print_banner()
    while True:
        _menu()
        choice = console.input("[bold cyan]huginn[/bold cyan] ").strip().lower()
        if not choice:
            continue
        if choice == "q":
            console.print("[dim]Goodbye.[/dim]")
            break
        elif choice == "s":
            _run_async(_i_scrape())
        elif choice == "c":
            _run_async(_i_crawl())
        elif choice == "e":
            _run_async(_i_extract())
        elif choice == "r":
            _run_async(_i_search())
        elif choice == "m":
            _run_async(_i_map())
        elif choice == "d":
            _run_async(_i_research())
        elif choice == "v":
            _run_async(_i_serve())
        elif choice == "t":
            templates_cmd()
        elif choice == "b":
            _run_async(_i_batch())
        elif choice == "M":
            _run_async(_i_memory())
        elif choice == "W":
            _run_async(_i_watch())
        elif choice == "j":
            _run_async(_i_jobs())
        elif choice == "o":
            config_cmd()
        elif choice == "h":
            _run_async(_doctor())
        console.print()



async def _i_batch():
    src = Prompt.ask("URL source file (or '-' for stdin)", default="urls.txt", console=console)
    op = Prompt.ask("Operation", choices=["scrape", "extract", "map"], default="scrape", console=console)
    tpl_name = ""
    if op == "extract":
        tpl_name = Prompt.ask("Template (empty for none)", default="", console=console)
    out = Prompt.ask("Output file (empty for stdout)", default="", console=console)
    await _do_batch(src, op, tpl_name or None, out or "-", "json", 1)

async def _i_memory():
    sub = Prompt.ask("Memory command", choices=["query", "reports", "related", "delete"],
                     default="query", console=console)
    if sub == "query":
        q = Prompt.ask("Query text", console=console)
        n = int(Prompt.ask("Max results", default="10", console=console))
        await _memory_query(q, n, "-", "json")
    elif sub == "reports":
        await _memory_reports("-", "json")
    elif sub == "related":
        topic = Prompt.ask("Topic", console=console)
        await _memory_related(topic, 10, "-", "json")
    elif sub == "delete":
        rid = Prompt.ask("Report ID", console=console)
        await _memory_delete(rid)

async def _i_watch():
    sub = Prompt.ask("Watch command", choices=["add", "list", "check", "remove"],
                     default="list", console=console)
    if sub == "add":
        url = Prompt.ask("URL to watch", console=console)
        sel = Prompt.ask("CSS selector (empty for whole page)", default="", console=console)
        await _watch_add(url, sel or None, 3600, None)
    elif sub == "list":
        await _watch_list("-", "json")
    elif sub == "check":
        url = Prompt.ask("URL to check", console=console)
        await _watch_check(url)
    elif sub == "remove":
        url = Prompt.ask("URL to remove", console=console)
        await _watch_remove(url)

async def _i_jobs():
    await _jobs_list("-", "json")

async def _i_serve():
    port = Prompt.ask("Port", default="7432", console=console)
    host = Prompt.ask("Host", default="0.0.0.0", console=console)
    console.print(f"[green]Starting server on {host}:{port}[/green]")
    import uvicorn
    from .api import create_app
    from .config import load_config
    cfg = load_config()
    cfg.server.host = host
    cfg.server.port = int(port)
    uvicorn.run(create_app(cfg), host=host, port=int(port), log_level="info")


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def main():
    cli()


if __name__ == "__main__":
    main()
