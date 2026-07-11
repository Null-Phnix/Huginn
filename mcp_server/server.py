"""
Huginn MCP Server

A Model Context Protocol server that exposes Huginn web intelligence tools
(probe, sweep, chart, seek, distill, flock) to MCP clients.

Uses the `mcp` Python package (modelcontextprotocol/python-sdk).

Environment Variables:
    HUGINN_BASE_URL: Base URL of the Huginn API (default: http://localhost:7432)
    HUGINN_API_KEY:  API key for Huginn authentication
    HUGINN_POLL_INTERVAL: Seconds between job status polls (default: 2)
    HUGINN_POLL_TIMEOUT:  Max seconds to wait for job completion (default: 300)
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP

# ─── Settings ──────────────────────────────────────────────────────────────────

HUGINN_BASE_URL = os.getenv("HUGINN_BASE_URL", "http://localhost:7432").rstrip("/")
HUGINN_POLL_INTERVAL = float(os.getenv("HUGINN_POLL_INTERVAL", "2"))
HUGINN_POLL_TIMEOUT = float(os.getenv("HUGINN_POLL_TIMEOUT", "300"))

# ─── HTTP Helpers ─────────────────────────────────────────────────────────────


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    api_key = os.getenv("HUGINN_API_KEY", "").strip()
    api_key_file = Path(
        os.getenv("HUGINN_API_KEY_FILE", "~/.config/huginn/api-key")
    ).expanduser()
    if not api_key and api_key_file.is_file():
        api_key = api_key_file.read_text(encoding="utf-8").strip()
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


async def _post_json(path: str, json_data: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{HUGINN_BASE_URL}{path}",
            json=json_data,
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def _get_json(path: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(
            f"{HUGINN_BASE_URL}{path}",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def _delete_json(path: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.delete(
            f"{HUGINN_BASE_URL}{path}",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


# ─── Job Polling ──────────────────────────────────────────────────────────────


async def _poll_job(
    path: str,
    ctx: Context | None = None,
    poll_interval: float = HUGINN_POLL_INTERVAL,
    timeout: float = HUGINN_POLL_TIMEOUT,
) -> dict[str, Any]:
    """
    Poll a Huginn job endpoint until it reaches a terminal state.

    Args:
        path: The status endpoint to poll (e.g. /v1/sweep/{job_id})
        ctx:  Optional MCP context for logging
        poll_interval: Seconds between polls
        timeout: Max seconds before TimeoutError

    Returns the full job result dict.
    Raises asyncio.TimeoutError if the job does not complete within `timeout`.
    """
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        job = await _get_json(path)
        status = str(job.get("status", job.get("data", {}).get("status", ""))).lower()

        if ctx is not None:
            ctx.debug(f"Job {path} status: {status}")

        if status in ("completed", "success", "done"):
            return job
        if status in ("failed", "error", "cancelled"):
            return job

        await asyncio.sleep(poll_interval)

    raise asyncio.TimeoutError(f"Job at {path} did not complete within {timeout}s")


# ─── FastMCP Server ───────────────────────────────────────────────────────────

mcp = FastMCP(
    name="Huginn",
    instructions=(
        "Huginn MCP Server — autonomous web intelligence. Provides: probe (single URL), "
        "sweep (deep crawl), chart (sitemap), seek (search), distill (structured extraction), "
        "flock (batch scrape). Connect to a Huginn API instance (default http://localhost:7432)."
    ),
)


# ─── Tool: probe ──────────────────────────────────────────────────────────────


@mcp.tool(
    name="probe",
    description=(
        "Scrape a single URL and return content in requested formats (markdown, html, links, etc.). "
        "Use this for quick, lightweight page inspection without full crawling."
    ),
)
async def probe(
    url: str,
    formats: list[str] | None = None,
    only_main_content: bool = True,
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """
    Scrape a single URL.

    Args:
        url:               The URL to scrape.
        formats:           Output formats (markdown, html, raw_html, screenshot, links, metadata, pdf).
        only_main_content: Filter to main content (default True).
        include_tags:      Comma-separated tag list to include.
        exclude_tags:      Comma-separated tag list to exclude.
    """
    if ctx:
        ctx.info(f"Probing {url}")

    payload: dict[str, Any] = {"url": url, "only_main_content": only_main_content}
    if formats:
        payload["formats"] = formats
    if include_tags:
        payload["include_tags"] = include_tags
    if exclude_tags:
        payload["exclude_tags"] = exclude_tags

    return await _post_json("/v1/probe", payload)


# ─── Tool: sweep ──────────────────────────────────────────────────────────────


@mcp.tool(
    name="sweep",
    description=(
        "Deep crawl a website starting from a URL, following links to discover pages. "
        "Submits a crawl job, polls until completion, returns full crawled data. "
        "For quick single-page scraping use 'probe' instead."
    ),
)
async def sweep(
    url: str,
    max_depth: int = 2,
    max_pages: int = 50,
    formats: list[str] | None = None,
    webhook_url: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """
    Deep crawl a website.

    Args:
        url:        Starting URL for the crawl.
        max_depth:  How deep to follow links (default 2).
        max_pages:  Maximum unique pages to collect (default 50).
        formats:    Output formats per page.
        webhook_url: Optional webhook for completion notification.
    """
    if ctx:
        ctx.info(f"Starting sweep of {url} (depth={max_depth}, max_pages={max_pages})")

    payload: dict[str, Any] = {
        "url": url,
        "max_depth": max_depth,
        "limit": max_pages,
        "scrape_options": {"formats": formats or ["markdown"]},
    }
    if webhook_url:
        payload["webhook_url"] = webhook_url

    submit = await _post_json("/v1/sweep", payload)
    job_id = submit.get("id") or submit.get("job_id")
    if not job_id:
        raise ValueError(f"Huginn returned no job_id: {submit}")

    if ctx:
        ctx.info(f"Crawl job submitted: {job_id}")

    result = await _poll_job(f"/v1/sweep/{job_id}", ctx)

    if ctx:
        ctx.info(f"Crawl job {job_id} completed")

    return result


# ─── Tool: chart ──────────────────────────────────────────────────────────────


@mcp.tool(
    name="chart",
    description="Generate a site map / chart of links from a root URL.",
)
async def chart(
    url: str,
    search: str | None = None,
    include_subdomains: bool = False,
    limit: int = 100,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """
    Generate a site map.

    Args:
        url:                Root URL to chart.
        search:             Limit to pages matching this search term.
        include_subdomains: Include subdomains (default False).
        limit:             Max links to return (default 100).
    """
    if ctx:
        ctx.info(f"Charting {url}")

    payload: dict[str, Any] = {"url": url, "limit": limit}
    if search:
        payload["search"] = search
    if include_subdomains:
        payload["include_subdomains"] = True

    submit = await _post_json("/v1/chart", payload)
    job_id = submit.get("job_id")
    if not job_id:
        return submit  # Some chart endpoints are sync

    result = await _poll_job(f"/v1/chart/{job_id}", ctx)
    return result


# ─── Tool: seek ────────────────────────────────────────────────────────────────


@mcp.tool(
    name="seek",
    description=(
        "Search the web and return ranked results with snippets. "
        "For deep crawling of result pages use 'sweep' instead."
    ),
)
async def seek(
    query: str,
    limit: int = 10,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """
    Web search.

    Args:
        query: Search query string.
        limit: Number of results (default 10).
    """
    if ctx:
        ctx.info(f"Seeking: {query}")

    payload: dict[str, Any] = {
        "query": query,
        "search_options": {"limit": limit},
    }

    return await _post_json("/v1/seek", payload)


# ─── Tool: distill ──────────────────────────────────────────────────────────────


@mcp.tool(
    name="distill",
    description=(
        "Extract structured data from one or more URLs using a JSON schema or natural language prompt. "
        "Submits an extraction job, polls until completion, returns structured result. "
        "For simple scraping use 'probe' instead."
    ),
)
async def distill(
    urls: list[str],
    prompt: str,
    schema: dict[str, Any] | None = None,
    format: str = "markdown",
    webhook_url: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """
    Extract structured data from URLs.

    Args:
        urls:       List of URLs to extract from.
        prompt:     Natural language instruction for what to extract.
        schema:     Optional JSON Schema for structured output.
        format:     Output format — text, markdown, or json (requires schema).
        webhook_url: Optional webhook for completion notification.
    """
    if ctx:
        ctx.info(f"Distilling {len(urls)} URL(s) with prompt: {prompt}")

    payload: dict[str, Any] = {
        "urls": urls,
        "prompt": prompt,
        "format": format,
    }
    if schema:
        payload["schema_"] = schema
    if webhook_url:
        payload["webhook_url"] = webhook_url

    submit = await _post_json("/v1/distill", payload)
    job_id = submit.get("id") or submit.get("job_id")
    if not job_id:
        raise ValueError(f"Huginn returned no job_id: {submit}")

    if ctx:
        ctx.info(f"Distill job submitted: {job_id}")

    result = await _poll_job(f"/v1/distill/{job_id}", ctx)

    if ctx:
        ctx.info(f"Distill job {job_id} completed")

    return result


# ─── Tool: flock ────────────────────────────────────────────────────────────────


@mcp.tool(
    name="flock",
    description=(
        "Batch scrape multiple URLs in parallel. "
        "Returns the synchronous batch result for all URLs. "
        "For sequential single-URL scraping use 'probe' instead."
    ),
)
async def flock(
    urls: list[str],
    formats: list[str] | None = None,
    only_main_content: bool = True,
    webhook_url: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """
    Batch scrape multiple URLs.

    Args:
        urls:               List of URLs to scrape.
        formats:            Output formats (markdown, html, links, etc.).
        only_main_content: Filter to main content (default True).
        webhook_url:        Optional webhook for completion notification.
    """
    if ctx:
        ctx.info(f"Flocking {len(urls)} URLs")

    payload: dict[str, Any] = {
        "urls": urls,
        "only_main_content": only_main_content,
    }
    if formats:
        payload["formats"] = formats
    if webhook_url:
        payload["webhook_url"] = webhook_url

    return await _post_json("/v1/flock", payload)


# ─── Tool: jobs ────────────────────────────────────────────────────────────────


@mcp.tool(
    name="jobs",
    description="List recent Huginn jobs or get details for a specific job.",
)
async def list_jobs(
    job_id: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """
    List jobs or get a specific job.

    Args:
        job_id: If provided, get details for this job. Otherwise list recent jobs.
    """
    if job_id:
        return await _get_json(f"/v1/jobs/{job_id}")
    return await _get_json("/v1/jobs")


# ─── Main Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")


# ─── Entry Point ───────────────────────────────────────────────────────────────

def main():
    """Entry point for huginn-mcp CLI."""
    mcp.run(transport="stdio")
