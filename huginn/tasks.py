"""
Huginn background task runners and SSE streaming generators.

Moved from api.py during the v1.5 router split.  All functions access
shared state through get_state() instead of module-level globals.
"""

import asyncio
import inspect
import json
import logging
import uuid
from typing import List

from .crawler import Crawler
from .extractor import Extractor
from .models import (
    CrawlRequest,
    DistillRequest,
    FlockRequest,
    FlockResultItem,
    OutputFormat,
)
from .scraper import Scraper
from .state import get_state
from .utils import (
    _map_exception_to_error_code,
    build_proxy_dict,
    scrape_failure,
    scrape_options_kwargs,
    sse_event,
)
from .webhook import fire_webhook_for_job

logger = logging.getLogger(__name__)


async def _close_scraper(scraper) -> None:
    """Close real scraper resources while remaining friendly to simple test doubles."""
    close = getattr(scraper, "close", None)
    if not close:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


def _crawl_scrape_kwargs(req: CrawlRequest) -> dict:
    """Translate every CrawlRequest.scrapeOptions field into Scraper kwargs."""
    kwargs = scrape_options_kwargs(req.scrape_options)
    kwargs.pop("only_main_content", None)
    kwargs.pop("timeout", None)
    return kwargs


async def schedule_handler(request: dict):
    """Handle a scheduled job firing. Scheduler passes already-parsed dict."""
    state = get_state()
    job_type = request.get("job_type", "crawl")
    request_dict = request.get("request", {})

    job_id = str(uuid.uuid4())

    await state.job_store.create_job({
        "id": job_id,
        "type": job_type,
        "status": "queued",
        "request": request_dict,
    })

    if job_type == "crawl":
        from .models import CrawlRequest
        req = CrawlRequest(**request_dict)
        await run_crawl(job_id, req)
    elif job_type == "distill":
        from .models import DistillRequest
        req = DistillRequest(**request_dict)
        await run_distill(job_id, req)
    elif job_type == "flock":
        from .models import FlockRequest
        req = FlockRequest(**request_dict)
        await run_flock(job_id, req)


def register_scheduler_handlers():
    """Register handlers with the scheduler's registry."""
    from .scheduler import _HANDLERS
    _HANDLERS["crawl"] = schedule_handler
    _HANDLERS["distill"] = schedule_handler
    _HANDLERS["flock"] = schedule_handler


# ─── Background Task Runners ────────────────────────────────────────────────

async def run_crawl(job_id: str, req: CrawlRequest):
    """Background task for crawl jobs."""
    state = get_state()
    _config = state.config
    _browser = state.browser
    _job_store = state.job_store

    scraper_formats = []
    if req.scrape_options:
        scraper_formats = req.scrape_options.formats

    crawler = Crawler(
        browser=_browser,
        max_depth=req.max_depth or _config.crawl.max_depth,
        max_pages=req.limit or _config.crawl.max_pages,
        concurrency=req.max_concurrency or _config.crawl.concurrency,
        delay=_config.crawl.delay_between_requests,
        allow_external=req.allow_external_links,
        allow_backward=req.allow_backward_crawling,
        include_paths=req.include_paths,
        exclude_paths=req.exclude_paths,
        ignore_robots=req.ignore_robots,
        scrape_kwargs=_crawl_scrape_kwargs(req),
    )

    try:
        await _job_store.update_job(job_id, status="running")

        result = await crawler.crawl(
            start_url=req.url,
            scrape_formats=scraper_formats or [OutputFormat.MARKDOWN],
            only_main_content=req.scrape_options.only_main_content if req.scrape_options else True,
            timeout=req.scrape_options.timeout if req.scrape_options else _config.browser.navigation_timeout,
        )

        # Store results
        pages_data = []
        for page in result.pages:
            pages_data.append(page.model_dump(exclude_none=True))

        job_result = {"pages": pages_data}
        errors = list(getattr(result, "errors", []) or [])
        if errors:
            job_result["errors"] = errors
        await _job_store.update_job(
            job_id,
            status="completed",
            job_result=job_result,
            completed=result.completed,
            total=result.total_discovered,
        )

    except asyncio.CancelledError:
        await _job_store.update_job(job_id, status="cancelled", error="Job cancelled")
    except Exception as e:
        logger.error(f"Crawl job {job_id} failed: {e}", exc_info=True)
        await _job_store.update_job(job_id, status="failed", error=str(e))
    finally:
        await _close_scraper(crawler.scraper)
        state.crawl_tasks.pop(job_id, None)


async def run_distill(job_id: str, req: DistillRequest):
    """Background task for extract jobs."""
    import httpx
    state = get_state()
    _config = state.config
    _browser = state.browser
    _job_store = state.job_store

    http_client = httpx.AsyncClient(timeout=120, limits=httpx.Limits(max_connections=20))
    try:
        extractor = Extractor(
            browser=_browser,
            llm_provider=_config.extract.llm_provider,
            llm_model=_config.extract.llm_model,
            max_retries=req.max_retries,
            mental_model=req.mental_model,
            http_client=http_client,
        )

        await _job_store.update_job(job_id, status="running")

        from .templates import get_template
        template = get_template(req.template) if req.template else None

        result = await extractor.extract(
            urls=req.urls,
            prompt=req.prompt,
            schema=req.schema_,
            system_prompt=req.system_prompt,
            output_format=req.format,
            template=template,
            examples=req.examples,
        )

        await _job_store.update_job(
            job_id,
            status="completed",
            job_result=result,
        )

    except asyncio.CancelledError:
        await _job_store.update_job(job_id, status="cancelled", error="Job cancelled")
    except Exception as e:
        logger.error(f"Extract job {job_id} failed: {e}", exc_info=True)
        await _job_store.update_job(job_id, status="failed", error=str(e))
    finally:
        await http_client.aclose()
        state.crawl_tasks.pop(job_id, None)


# ─── Batch Task Runner ─────────────────────────────────────────────────────────

async def run_flock(job_id: str, req: FlockRequest):
    """Background task for batch scrape (flock) jobs."""
    from .circuit_breaker import get_circuit_breaker, extract_domain
    from .cache import get_cached_scrape_result, cache_scrape_result
    from .models import ErrorCode

    state = get_state()
    _config = state.config
    _browser = state.browser
    _job_store = state.job_store

    proxy_dict = build_proxy_dict(_config)
    cb = get_circuit_breaker()
    scraper = Scraper(_browser, cb)
    sem = asyncio.Semaphore(5)
    results: List[FlockResultItem] = []
    cache_context = req.model_dump(mode="json", by_alias=True, exclude_none=True)
    cache_context.pop("urls", None)
    cache_context.pop("formats", None)

    async def scrape_one(url: str) -> FlockResultItem:
        from .scraper import _is_valid_http_url
        if not _is_valid_http_url(url):
            return FlockResultItem(
                url=url,
                success=False,
                error=f"Invalid URL (bad scheme or missing host): {url}",
                error_code=ErrorCode.INVALID_URL,
            )
        async with sem:
            domain = extract_domain(url)
            if cb.is_open(domain):
                return FlockResultItem(
                    url=url, success=False,
                    error=f"Circuit breaker open for {domain}",
                    error_code=ErrorCode.CIRCUIT_OPEN,
                )

            cached = await get_cached_scrape_result(
                url,
                req.formats or [OutputFormat.MARKDOWN],
                extra=cache_context,
            )
            if cached:
                return FlockResultItem(url=url, success=True, data=cached, cached=True)

            try:
                data = await scraper.scrape(
                    url=url,
                    formats=req.formats,
                    include_tags=req.include_tags,
                    exclude_tags=req.exclude_tags,
                    only_main_content=req.only_main_content,
                    timeout=req.timeout,
                    proxy=proxy_dict,
                )
                failure = scrape_failure(data)
                if failure:
                    status, message = failure
                    await cb.record_failure(domain)
                    return FlockResultItem(
                        url=url,
                        success=False,
                        data=data,
                        error=message,
                        error_code=ErrorCode.from_http_status(status),
                    )
                if data.markdown or data.html or data.raw_html or data.links or data.screenshot:
                    await cache_scrape_result(
                        url,
                        req.formats or [OutputFormat.MARKDOWN],
                        data,
                        extra=cache_context,
                    )
                await cb.record_success(domain)
                return FlockResultItem(url=url, success=True, data=data)
            except asyncio.TimeoutError:
                await cb.record_failure(domain)
                return FlockResultItem(
                    url=url, success=False,
                    error=f"Request timed out after {req.timeout}ms",
                    error_code=ErrorCode.TIMEOUT,
                )
            except Exception as e:
                await cb.record_failure(domain)
                return FlockResultItem(
                    url=url, success=False,
                    error=str(e),
                    error_code=_map_exception_to_error_code(e),
                )

    try:
        await _job_store.update_job(job_id, status="running")

        tasks = [scrape_one(url) for url in req.urls]
        items = await asyncio.gather(*tasks, return_exceptions=True)

        for i, item in enumerate(items):
            if isinstance(item, FlockResultItem):
                results.append(item)
            elif isinstance(item, Exception):
                results.append(FlockResultItem(
                    url=req.urls[i] if i < len(req.urls) else "unknown",
                    success=False, error=str(item),
                    error_code=ErrorCode.NAVIGATION_FAILED,
                ))

        success_count = sum(1 for r in results if r.success)
        partial = success_count > 0 and success_count < len(results)

        await _job_store.update_job(
            job_id,
            status="completed",
            job_result={"results": [r.model_dump() for r in results], "partial": partial},
            completed=success_count,
            total=len(results),
        )

        if req.webhook_url:
            await fire_webhook_for_job(req.webhook_url, job_id, "flock", results)

    except asyncio.CancelledError:
        await _job_store.update_job(job_id, status="cancelled", error="Job cancelled")
    except Exception as e:
        logger.error(f"Flock job {job_id} failed: {e}", exc_info=True)
        await _job_store.update_job(job_id, status="failed", error=str(e))
    finally:
        await _close_scraper(scraper)
        state.crawl_tasks.pop(job_id, None)


# ─── SSE Streaming Generators ────────────────────────────────────────────────

async def stream_crawl(req: CrawlRequest):
    """SSE generator for crawl — yields document events and a final done event."""
    state = get_state()
    _config = state.config
    _browser = state.browser

    scraper_formats = []
    if req.scrape_options:
        scraper_formats = req.scrape_options.formats

    crawler = Crawler(
        browser=_browser,
        max_depth=req.max_depth or _config.crawl.max_depth,
        max_pages=req.limit or _config.crawl.max_pages,
        concurrency=req.max_concurrency or _config.crawl.concurrency,
        delay=_config.crawl.delay_between_requests,
        allow_external=req.allow_external_links,
        allow_backward=req.allow_backward_crawling,
        include_paths=req.include_paths,
        exclude_paths=req.exclude_paths,
        ignore_robots=req.ignore_robots,
        scrape_kwargs=_crawl_scrape_kwargs(req),
    )

    try:
        # Use an asyncio.Queue to relay pages from the crawl task
        page_queue: asyncio.Queue = asyncio.Queue()

        async def _crawl_and_enqueue():
            """Run crawl and put each page into the queue as it completes."""
            try:
                async def _on_page(page):
                    await page_queue.put(("document", page))

                result = await crawler.crawl(
                    start_url=req.url,
                    scrape_formats=scraper_formats or [OutputFormat.MARKDOWN],
                    only_main_content=req.scrape_options.only_main_content if req.scrape_options else True,
                    timeout=req.scrape_options.timeout if req.scrape_options else _config.browser.navigation_timeout,
                    on_page=_on_page,
                )
                await page_queue.put(("done", result))
            except Exception as e:
                logger.error(f"Stream crawl failed: {e}", exc_info=True)
                await page_queue.put(("error", str(e)))

        # Start the crawl task
        crawl_task = asyncio.create_task(_crawl_and_enqueue())

        # Yield events as they arrive
        result_obj = None
        while True:
            try:
                # Use a timeout so we can check if the task completed
                event_type, event_data = await asyncio.wait_for(page_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if crawl_task.done():
                    # Check if queue is empty and task is done
                    if page_queue.empty():
                        break
                    continue

            if event_type == "document":
                page_dict = event_data.model_dump(exclude_none=True)
                yield sse_event("document", {"type": "document", "data": page_dict})
            elif event_type == "done":
                result_obj = event_data
                break
            elif event_type == "error":
                yield sse_event("done", {"type": "done", "data": {"success": False, "status": "failed", "error": event_data}})
                return

        # Send final done event
        if result_obj:
            done_payload = {
                "type": "done",
                "data": {
                    "success": True,
                    "status": "completed",
                    "completed": result_obj.completed,
                    "total": result_obj.total_discovered,
                    "errors": list(getattr(result_obj, "errors", []) or []),

                },
            }
            yield sse_event("done", done_payload)

    except Exception as e:
        logger.error(f"SSE crawl stream error: {e}", exc_info=True)
        yield sse_event("done", {"type": "done", "data": {"success": False, "status": "failed", "error": str(e)}})
    finally:
        await _close_scraper(crawler.scraper)


async def jsonl_stream_crawl(req: CrawlRequest):
    """NDJSON (JSON Lines) generator — yields one JSON object per line as pages complete.

    Unlike SSE, NDJSON is simpler to parse: each line is a complete JSON object.
    Clients read line-by-line and parse incrementally without event parsing.
    """
    state = get_state()
    _config = state.config
    _browser = state.browser

    scraper_formats = []
    if req.scrape_options:
        scraper_formats = req.scrape_options.formats

    crawler = Crawler(
        browser=_browser,
        max_depth=req.max_depth or _config.crawl.max_depth,
        max_pages=req.limit or _config.crawl.max_pages,
        concurrency=req.max_concurrency or _config.crawl.concurrency,
        delay=_config.crawl.delay_between_requests,
        allow_external=req.allow_external_links,
        allow_backward=req.allow_backward_crawling,
        include_paths=req.include_paths,
        exclude_paths=req.exclude_paths,
        ignore_robots=req.ignore_robots,
        scrape_kwargs=_crawl_scrape_kwargs(req),
    )

    result_ref: list = [None]
    page_queue: asyncio.Queue = asyncio.Queue()

    async def _crawl_and_stream():
        """Run crawl with real-time page callback."""
        try:
            async def _relay(page_data):
                await page_queue.put(page_data)

            result = await crawler.crawl(
                start_url=req.url,
                scrape_formats=scraper_formats or [OutputFormat.MARKDOWN],
                only_main_content=req.scrape_options.only_main_content if req.scrape_options else True,
                timeout=req.scrape_options.timeout if req.scrape_options else _config.browser.navigation_timeout,
                on_page=_relay,
            )
            result_ref[0] = result
            await page_queue.put(None)  # sentinel
        except Exception as e:
            logger.error(f"JSONL crawl failed: {e}", exc_info=True)
            await page_queue.put(None)

    crawl_task = asyncio.create_task(_crawl_and_stream())

    try:
        while True:
            try:
                page_data = await asyncio.wait_for(page_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if crawl_task.done() and page_queue.empty():
                    break
                continue

            if page_data is None:
                break

            line = json.dumps(page_data.model_dump(exclude_none=True))
            yield line + "\n"

        # Final summary line
        if result_ref[0]:
            summary = {
                "type": "__done__",
                "success": True,
                "status": "completed",
                "completed": result_ref[0].completed,
                "total": result_ref[0].total_discovered,
                "errors": list(getattr(result_ref[0], "errors", []) or []),
            }
            yield json.dumps(summary) + "\n"
        else:
            yield json.dumps({"type": "__done__", "success": False, "status": "failed"}) + "\n"

    except Exception as e:
        logger.error(f"JSONL crawl stream error: {e}", exc_info=True)
        yield json.dumps({"type": "__done__", "success": False, "status": "failed", "error": str(e)}) + "\n"
    finally:
        await _close_scraper(crawler.scraper)


async def stream_distill(req: DistillRequest):
    """SSE generator for extract — yields progress events and a final done event."""
    state = get_state()
    _config = state.config
    _browser = state.browser

    extractor = Extractor(
        browser=_browser,
        llm_provider=_config.extract.llm_provider,
        llm_model=_config.extract.llm_model,
        max_retries=req.max_retries,
        mental_model=req.mental_model,
    )

    try:
        # Yield scraping progress events per URL
        total_urls = len(req.urls)
        scraped_data = []

        for i, url in enumerate(req.urls):
            # Emit scraping progress
            yield sse_event("progress", {
                "type": "progress",
                "data": {
                    "step": "scraping",
                    "url": url,
                    "current": i + 1,
                    "total": total_urls,
                },
            })

            # Scrape the URL
            try:
                page_data = await extractor.scraper.scrape(
                    url=url,
                    formats=[OutputFormat.MARKDOWN],
                    only_main_content=True,
                )
                if page_data and page_data.markdown:
                    scraped_data.append({
                        "url": url,
                        "title": page_data.metadata.get("title", "") if page_data.metadata else "",
                        "length": len(page_data.markdown),
                    })
            except Exception as e:
                logger.warning(f"Failed to scrape {url}: {e}")

        # Now run extraction with all scraped content
        yield sse_event("progress", {
            "type": "progress",
            "data": {
                "step": "extracting",
                "message": "Running LLM extraction",
                "urls_scraped": len(scraped_data),
                "total_urls": total_urls,
            },
        })

        from .templates import get_template
        template = get_template(req.template) if req.template else None
        result = await extractor.extract(
            urls=req.urls,
            prompt=req.prompt,
            schema=req.schema_,
            system_prompt=req.system_prompt,
            output_format=req.format,
            template=template,
            examples=req.examples,
        )

        # Send final done event with the result
        yield sse_event("done", {"type": "done", "data": result})

    except Exception as e:
        logger.error(f"SSE extract stream error: {e}", exc_info=True)
        yield sse_event("done", {"type": "done", "data": {"success": False, "status": "failed", "error": str(e)}})
