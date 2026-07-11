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
from typing import Any, List, Dict, Optional

import markdownify
from bs4 import BeautifulSoup

from .models import OutputFormat, ScrapeData


def _action_type(action: dict) -> str:
    """Normalize JSON strings and Pydantic Enum values to the wire action name."""
    value = action.get("type", "")
    value = getattr(value, "value", value)
    return str(value).lower()


def tcp_addr() -> Optional[str]:
    return os.environ.get("HUGINN_STARSEARCH_TCP") or None


async def daemon_status(timeout: float = 2.0) -> dict:
    """Return live daemon capacity without allocating a browser session."""
    addr = tcp_addr()
    if not addr:
        return {"configured": False, "reachable": False}
    host, _, port = addr.partition(":")
    writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port or 7676)), timeout=timeout
        )

        async def exchange(payload: dict) -> dict:
            writer.write((json.dumps(payload) + "\n").encode())
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            if not line:
                raise ConnectionError("daemon closed health connection")
            return json.loads(line.decode())

        handshake = await exchange({"starsearch": "1.0", "client_version": "huginn-health"})
        if not handshake.get("compatible"):
            raise RuntimeError("incompatible protocol")
        response = await exchange({"v": 1, "cmd": "status"})
        if not response.get("ok"):
            raise _response_error(response, "status")
        return {
            "configured": True,
            "reachable": True,
            "address": addr,
            **(response.get("result") or {}),
        }
    except Exception as exc:
        return {
            "configured": True,
            "reachable": False,
            "address": addr,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


def _response_error(response: dict, command: str) -> RuntimeError:
    detail = response.get("detail")
    message = response.get("error") or "unknown daemon error"
    if detail:
        message = f"{message}: {detail}"
    return RuntimeError(f"StarSearch {command} failed: {message}")


async def _fetch_page(
    addr: str,
    url: str,
    *,
    timeout: float = 45.0,
    actions: Optional[List[dict]] = None,
    wait_for: Optional[Any] = None,
    scroll: bool = False,
    screenshot: bool = False,
    cookies: Optional[Dict[str, str]] = None,
    proxy: Optional[Dict[str, str]] = None,
    locale: str = "en-US",
    allow_private_network: bool = False,
) -> tuple[str, Optional[str]]:
    """Execute a complete scrape session over StarSearch's JSON-lines protocol.

    Every command response is checked.  The former bridge ignored navigation
    errors and then returned an empty/old document as a successful scrape.
    """
    host, _, port = addr.partition(":")
    # limit= raises asyncio's 64 KB readline cap so large SERPs/pages don't throw
    # "Separator is not found, and chunk exceed the limit".
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, int(port or 7676), limit=16 * 1024 * 1024),
        timeout=10.0,
    )

    async def send(obj: dict, *, expect_ok: bool = False) -> dict:
        writer.write((json.dumps(obj) + "\n").encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=timeout + 15)
        if not line:
            raise RuntimeError("StarSearch daemon closed the connection")
        response = json.loads(line.decode())
        if expect_ok and not response.get("ok"):
            raise _response_error(response, str(obj.get("cmd", "command")))
        return response

    async def command(name: str, **kwargs: Any) -> dict:
        return await send(
            {"v": 1, "cmd": name, "sid": sid, **kwargs},
            expect_ok=True,
        )

    async def execute_action(action: dict) -> Optional[str]:
        action_type = _action_type(action)
        selector = action.get("selector")
        if action_type == "click" and selector:
            await command("click", selector=selector, human=True)
        elif action_type == "type" and selector:
            await command(
                "type", selector=selector, text=str(action.get("text") or ""), human=True
            )
        elif action_type == "scroll":
            await command(
                "scroll",
                direction=str(action.get("direction") or "down"),
                amount=int(action.get("amount") or 500),
            )
        elif action_type == "hover" and selector:
            await command("hover", selector=selector)
        elif action_type == "wait_for_selector" and selector:
            timeout_ms = int(action.get("timeout") or 10_000)
            await command("wait_for", selector=selector, timeout_s=max(1, timeout_ms // 1000))
        elif action_type == "wait":
            await asyncio.sleep(max(0, int(action.get("amount") or 1000)) / 1000)
        elif action_type == "press":
            key = str(action.get("key") or "Enter")
            selector_js = json.dumps(selector) if selector else "null"
            key_js = json.dumps(key)
            await command(
                "evaluate",
                script=(
                    "(() => { const el = "
                    f"({selector_js} ? document.querySelector({selector_js}) : document.activeElement);"
                    f" if (!el) throw new Error('press target missing');"
                    f" for (const t of ['keydown','keypress','keyup'])"
                    f" el.dispatchEvent(new KeyboardEvent(t, {{key:{key_js}, bubbles:true}}));"
                    " return true; })()"
                ),
            )
        elif action_type == "select" and selector:
            values = action.get("values") or []
            await command(
                "evaluate",
                script=(
                    "(() => { const el = document.querySelector("
                    f"{json.dumps(selector)}); if (!el) throw new Error('select target missing');"
                    f" const wanted = new Set({json.dumps(values)});"
                    " for (const option of el.options) option.selected = wanted.has(option.value);"
                    " el.dispatchEvent(new Event('input', {bubbles:true}));"
                    " el.dispatchEvent(new Event('change', {bubbles:true})); return true; })()"
                ),
            )
        elif action_type == "screenshot":
            response = await command("screenshot")
            return (response.get("result") or {}).get("data")
        elif action_type:
            raise ValueError(f"Unsupported StarSearch action: {action_type}")
        return None

    sid = None
    try:
        hs = await send({"starsearch": "1.0", "client_version": "huginn"})
        if not hs.get("compatible"):
            raise RuntimeError(f"handshake incompatible: {hs}")
        proxy_server = proxy.get("server") if proxy else None
        r = await send(
            {
                "v": 1,
                "cmd": "new_session",
                "sid": None,
                "opts": {
                    "proxy": proxy_server,
                    "locale": locale,
                    "human_level": 1,
                    "capabilities": {
                        "navigate": True,
                        "form_submit": True,
                        "block_internal_network": True,
                        "allow_internal_network": allow_private_network,
                    },
                },
            },
            expect_ok=True,
        )
        sid = r.get("sid")
        if not sid:
            raise RuntimeError("StarSearch new_session returned no session id")

        if cookies:
            domain = urllib.parse.urlparse(url).hostname or ""
            cookie_params = [
                {"name": name, "value": value, "domain": domain, "path": "/"}
                for name, value in cookies.items()
            ]
            await command("set_cookies", cookies=cookie_params)

        await command("navigate", url=url, timeout_s=max(1, int(timeout)))

        if wait_for is not None:
            if isinstance(wait_for, (int, float)):
                await asyncio.sleep(max(0, float(wait_for)) / 1000)
            elif str(wait_for) not in {"networkidle", "network-idle", "network_idle", "domcontentloaded"}:
                await command("wait_for", selector=str(wait_for), timeout_s=max(1, int(timeout)))

        captured: Optional[str] = None
        for action in actions or []:
            action_screenshot = await execute_action(action)
            if action_screenshot:
                captured = action_screenshot

        if scroll:
            previous_height = -1
            for _ in range(10):
                height_response = await command(
                    "evaluate", script="document.body ? document.body.scrollHeight : 0"
                )
                height = ((height_response.get("result") or {}).get("value") or 0)
                if height == previous_height:
                    break
                previous_height = height
                await command("scroll", direction="down", amount=900)
                await asyncio.sleep(0.35)

        gc = await command("get_content")
        content = gc.get("result") or {}
        html = content if isinstance(content, str) else (content.get("html") or "")
        if screenshot and not captured:
            shot = await command("screenshot")
            captured = (shot.get("result") or {}).get("data")
        return html, captured
    finally:
        try:
            if sid:
                await send({"v": 1, "cmd": "close_session", "sid": sid}, expect_ok=True)
        except Exception:
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def _html_to_scrapedata(
    html: str,
    url: str,
    formats: List[OutputFormat],
    only_main_content: bool,
    *,
    include_tags: Optional[List[str]] = None,
    exclude_tags: Optional[List[str]] = None,
    screenshot: Optional[str] = None,
) -> ScrapeData:
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
    if include_tags:
        selected = []
        for selector in include_tags:
            selected.extend(content_el.select(selector))
        selected_soup = BeautifulSoup("<main></main>", "html.parser")
        selected_root = selected_soup.main
        for element in selected:
            selected_root.append(BeautifulSoup(str(element), "html.parser"))
        content_el = selected_root
    for selector in exclude_tags or []:
        for element in content_el.select(selector):
            element.decompose()
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
        elif fmt == OutputFormat.SCREENSHOT:
            result.screenshot = screenshot
    return result


async def scrape(
    url: str,
    formats: Optional[List[OutputFormat]] = None,
    only_main_content: bool = True,
    timeout: float = 45.0,
    retries: int = 3,
    *,
    actions: Optional[List[dict]] = None,
    wait_for: Optional[Any] = None,
    scroll: bool = False,
    include_tags: Optional[List[str]] = None,
    exclude_tags: Optional[List[str]] = None,
    cookies: Optional[Dict[str, str]] = None,
    proxy: Optional[Dict[str, str]] = None,
    locale: str = "en-US",
    allow_private_network: bool = False,
) -> Optional[ScrapeData]:
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
            wants_screenshot = OutputFormat.SCREENSHOT in formats or any(
                _action_type(action) == "screenshot"
                for action in actions or []
            )
            html, screenshot_data = await _fetch_page(
                addr,
                url,
                timeout=timeout,
                actions=actions,
                wait_for=wait_for,
                scroll=scroll,
                screenshot=wants_screenshot,
                cookies=cookies,
                proxy=proxy,
                locale=locale,
                allow_private_network=allow_private_network,
            )
            if not html:
                return None
            return _html_to_scrapedata(
                html,
                url,
                formats,
                only_main_content,
                include_tags=include_tags,
                exclude_tags=exclude_tags,
                screenshot=screenshot_data,
            )
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
            html, _ = await _fetch_page(addr, url)
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
