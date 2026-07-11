"""
Fetch pages through the StarSearch anti-detect browser daemon (over TCP) instead
of Huginn's own Playwright. StarSearch drives a real stealth Chromium, so it gets
past bot walls that block plain HTTP — keyless, no paid anti-bot service.

Enabled by setting HUGINN_STARSEARCH_TCP=host:port (e.g. 127.0.0.1:7676). The
Huginn container reaches the host daemon over TCP because it runs network_mode:
host. Falls through to Playwright on any failure.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import urllib.parse
from typing import List, Dict, Optional

import markdownify
from bs4 import BeautifulSoup

from .models import OutputFormat, ScrapeData


def tcp_addr() -> Optional[str]:
    return os.environ.get("HUGINN_STARSEARCH_TCP") or None


async def _fetch_html(addr: str, url: str, timeout: float = 45.0) -> str:
    """new_session -> navigate -> get_content -> close_session over the daemon's TCP JSON protocol."""
    host, _, port = addr.partition(":")
    # limit= raises asyncio's 64 KB readline cap so large SERPs/pages don't throw
    # "Separator is not found, and chunk exceed the limit".
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, int(port or 7676), limit=16 * 1024 * 1024),
        timeout=10.0,
    )

    async def send(obj: dict) -> dict:
        writer.write((json.dumps(obj) + "\n").encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=timeout + 15)
        return json.loads(line.decode())

    sid = None
    try:
        hs = await send({"starsearch": "1.0", "client_version": "huginn"})
        if not hs.get("compatible"):
            raise RuntimeError(f"handshake incompatible: {hs}")
        r = await send({"v": 1, "cmd": "new_session", "sid": None, "opts": {"human_level": 1}})
        sid = r.get("sid")
        if not r.get("ok") or not sid:
            raise RuntimeError(f"new_session failed: {r.get('error')}")
        await send({"v": 1, "cmd": "navigate", "sid": sid, "url": url, "timeout_s": int(timeout)})
        gc = await send({"v": 1, "cmd": "get_content", "sid": sid})
        c = gc.get("result") or gc.get("content") or ""
        return c if isinstance(c, str) else (c.get("html") or c.get("text") or "")
    finally:
        try:
            if sid:
                await send({"v": 1, "cmd": "close_session", "sid": sid})
        except Exception:
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def _html_to_scrapedata(html: str, url: str, formats: List[OutputFormat],
                        only_main_content: bool) -> ScrapeData:
    """Mirror Scraper.lightweight_scrape's HTML -> ScrapeData conversion."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    desc = soup.find("meta", attrs={"name": "description"})
    description = desc.get("content", "") if desc else ""
    lang_tag = soup.find("html")
    language = lang_tag.get("lang", "en") if lang_tag else "en"

    result = ScrapeData(metadata={
        "url": url, "title": title, "description": description,
        "language": language, "status_code": 200, "render_mode": "starsearch",
    })

    main = soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"})
    content_el = main if (main and only_main_content) else (soup.body or soup)
    for tag in content_el.find_all(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    for tag in content_el.find_all(class_=lambda c: c and any(
            x in c.lower() for x in ["sidebar", "nav", "ad", "cookie", "popup", "modal"])):
        tag.decompose()

    for fmt in formats:
        if fmt == OutputFormat.MARKDOWN:
            md = markdownify.markdownify(str(content_el), heading_style="ATX", bullets="-")
            result.markdown = re.sub(r"\n{3,}", "\n\n", md).strip()[:500_000]
        elif fmt == OutputFormat.HTML:
            result.html = str(content_el)
        elif fmt == OutputFormat.RAW_HTML:
            result.raw_html = html
        elif fmt == OutputFormat.LINKS:
            result.links = [a.get("href") for a in content_el.find_all("a", href=True)]
    return result


async def scrape(url: str, formats: Optional[List[OutputFormat]] = None,
                 only_main_content: bool = True, timeout: float = 45.0,
                 retries: int = 3) -> Optional[ScrapeData]:
    """Scrape via StarSearch. Returns ScrapeData, or None if unavailable/blocked.

    Retries transient failures (CapacityExceeded under crawl load, a crashed
    session, a dropped connection) with backoff. Empty HTML (e.g. an SSRF-blocked
    or genuinely blank page) is treated as permanent — no retry, returns None.
    """
    addr = tcp_addr()
    if not addr:
        return None
    formats = formats or [OutputFormat.MARKDOWN]
    for attempt in range(retries):
        try:
            html = await _fetch_html(addr, url, timeout=timeout)
            if not html:
                return None
            return _html_to_scrapedata(html, url, formats, only_main_content)
        except Exception:
            if attempt == retries - 1:
                raise
            await asyncio.sleep(0.4 * (attempt + 1))


def _decode_bing_url(href: str) -> str:
    """Bing wraps results in /ck/a redirects with the target base64 in u=a1<b64>."""
    if "bing.com/ck/a" not in href:
        return href
    u = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("u", [""])[0]
    if u.startswith("a1"):
        b = u[2:] + "=" * (-len(u[2:]) % 4)
        try:
            return base64.urlsafe_b64decode(b).decode("utf-8", "ignore")
        except Exception:
            return href
    return href


def _parse_bing_serp(html: str, limit: int) -> List[Dict[str, str]]:
    """Parse a Bing SERP into [{title, link, snippet}] (link key matches _scrape_results)."""
    from bs4 import BeautifulSoup as _BS
    soup = _BS(html, "html.parser")
    out: List[Dict[str, str]] = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a or not a.get("href"):
            continue
        cap = li.select_one(".b_caption p") or li.select_one("p")
        out.append({
            "title": a.get_text(" ", strip=True),
            "link": _decode_bing_url(a["href"]),
            "snippet": cap.get_text(" ", strip=True) if cap else "",
        })
        if len(out) >= limit:
            break
    return out


async def search_bing(query: str, limit: int = 5, retries: int = 3) -> List[Dict[str, str]]:
    """Keyless web search via StarSearch -> Bing. Returns [{title, link, snippet}] or []."""
    addr = tcp_addr()
    if not addr:
        return []
    url = "https://www.bing.com/search?q=" + urllib.parse.quote_plus(query)
    for attempt in range(retries):
        try:
            html = await _fetch_html(addr, url)
            return _parse_bing_serp(html, limit) if html else []
        except Exception:
            if attempt == retries - 1:
                return []
            await asyncio.sleep(0.4 * (attempt + 1))
    return []


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    os.environ.setdefault("HUGINN_STARSEARCH_TCP", "127.0.0.1:7676")
    data = asyncio.run(scrape(target))
    assert data is not None, "StarSearch scrape returned None (daemon down?)"
    print("render_mode:", data.metadata.get("render_mode"), "| title:", data.metadata.get("title"))
    print("markdown[:300]:", (data.markdown or "")[:300])
    assert data.markdown, "expected markdown content"
    print("PASS")
