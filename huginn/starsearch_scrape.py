"""
Fetch pages through the StarSearch anti-detect browser daemon (over TCP) instead
of Huginn's own Playwright. StarSearch drives a real stealth Chromium, so it gets
past bot walls that block plain HTTP — keyless, no paid anti-bot service.

Enabled by setting HUGINN_STARSEARCH_TCP=host:port (e.g. 127.0.0.1:7676). The
Huginn container reaches the host daemon over TCP because it runs network_mode:
host. This bridge reports StarSearch failures to its caller; production does
not silently fall through to Playwright. Compatibility fallback is a separate,
explicit BrowserManager setting and image variant.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import urllib.parse
from typing import Any, Dict, List, Optional

import markdownify
from bs4 import BeautifulSoup

from .config import read_secret_file
from .models import OutputFormat, ScrapeData
from .proxy import ProxyEndpoint


def _handshake(client_version: str) -> dict:
    payload = {"starsearch": "1.0", "client_version": client_version}
    token = os.environ.get("HUGINN_STARSEARCH_TOKEN")
    token_file = os.environ.get("HUGINN_STARSEARCH_TOKEN_FILE")
    if token_file:
        token = read_secret_file(
            token_file,
            "HUGINN_STARSEARCH_TOKEN_FILE",
            "HUGINN_SECRET_OWNER_UID",
        )
    if token:
        payload["auth_token"] = token
    return payload


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

        handshake = await exchange(_handshake("huginn-health"))
        if not handshake.get("compatible"):
            raise RuntimeError(handshake.get("error") or "incompatible protocol")
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


async def daemon_command(payload: dict, timeout: float = 30.0) -> dict:
    """Send one authenticated command over a short-lived TCP connection."""
    addr = tcp_addr()
    if not addr:
        raise RuntimeError("StarSearch TCP is not configured")
    host, _, port = addr.partition(":")
    writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port or 7676), limit=16 * 1024 * 1024),
            timeout=min(timeout, 10.0),
        )

        async def exchange(message: dict) -> dict:
            writer.write((json.dumps(message) + "\n").encode())
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            if not line:
                raise ConnectionError("StarSearch daemon closed the connection")
            return json.loads(line.decode())

        handshake = await exchange(_handshake("huginn-session-api"))
        if not handshake.get("compatible"):
            raise RuntimeError(handshake.get("error") or "incompatible StarSearch protocol")
        response = await exchange(payload)
        if not response.get("ok"):
            raise _response_error(response, str(payload.get("cmd", "command")))
        return response
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


def validate_egress_descriptor(
    raw: Any,
    *,
    proxy_configured: bool,
    expected_upstream_identity: Optional[str] = None,
) -> dict[str, Any]:
    """Validate StarSearch's authoritative socket-egress contract.

    Huginn must not infer enforcement from the proxy options it sent.  A daemon
    missing this descriptor is an older/unsafe runtime and is rejected before
    navigation, rather than silently degrading to Chromium-managed networking.
    """
    if not isinstance(raw, dict):
        raise ValueError("StarSearch session is missing its egress descriptor")
    expected_mode = "upstream" if proxy_configured else "direct"
    allowed_schemes = {"http", "https", "socks5"}
    if raw.get("gateway_enforced") is not True:
        raise ValueError("StarSearch did not confirm socket-enforced egress")
    if raw.get("mode") != expected_mode:
        raise ValueError(
            f"StarSearch egress mode mismatch: expected {expected_mode}, got {raw.get('mode')!r}"
        )
    if raw.get("resolution") != "local_frozen":
        raise ValueError("StarSearch did not confirm locally frozen destination resolution")
    scheme = raw.get("upstream_scheme")
    identity = raw.get("upstream_identity")
    if proxy_configured:
        if scheme not in allowed_schemes:
            raise ValueError("StarSearch returned an invalid upstream proxy scheme")
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise ValueError("StarSearch returned an invalid upstream routing identity")
        if identity != expected_upstream_identity:
            raise ValueError("StarSearch upstream routing identity does not match its proxy lease")
    elif scheme is not None or identity is not None:
        raise ValueError("StarSearch direct egress unexpectedly reported an upstream proxy")
    return {
        "gateway_enforced": True,
        "mode": expected_mode,
        "upstream_scheme": scheme,
        "upstream_identity": identity,
        "resolution": "local_frozen",
    }


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
) -> tuple[str, Optional[str], dict[str, Any]]:
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
        hs = await send(_handshake("huginn"))
        if not hs.get("compatible"):
            raise RuntimeError(f"handshake rejected: {hs.get('error') or 'incompatible protocol'}")
        proxy_options: Any = proxy if proxy else None
        r = await send(
            {
                "v": 1,
                "cmd": "new_session",
                "sid": None,
                "opts": {
                    "proxy": proxy_options,
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
        egress = validate_egress_descriptor(
            (r.get("result") or {}).get("egress"),
            proxy_configured=bool(proxy),
            expected_upstream_identity=(
                ProxyEndpoint.parse(
                    proxy["server"],
                    username=proxy.get("username"),
                    password=proxy.get("password"),
                ).starsearch_identity
                if proxy
                else None
            ),
        )

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
        return html, captured, egress
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
    egress: Optional[dict[str, Any]] = None,
) -> ScrapeData:
    """Mirror Scraper.lightweight_scrape's HTML -> ScrapeData conversion."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    desc = soup.find("meta", attrs={"name": "description"})
    description = desc.get("content", "") if desc else ""
    lang_tag = soup.find("html")
    language = lang_tag.get("lang", "en") if lang_tag else "en"

    metadata = {
        "url": url,
        "title": title,
        "description": description,
        "language": language,
        "status_code": 200,
        "render_mode": "starsearch",
    }
    if egress is not None:
        metadata["egress"] = egress
    result = ScrapeData(metadata=metadata)

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
            html, screenshot_data, egress = await _fetch_page(
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
                egress=egress,
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


def _normalize_result_url(
    href: str,
    *,
    base_url: str,
    decode_bing: bool = False,
) -> str:
    """Return a safe absolute HTTP(S) destination from a SERP link.

    Search pages contain relative navigation links, tracking wrappers, and
    occasionally non-navigation schemes.  API consumers must receive the real
    destination URL so a follow-up scrape never silently navigates back into a
    search engine or executes a ``javascript:``/``data:`` URL.
    """
    candidate = urllib.parse.urljoin(base_url, (href or "").strip())
    if decode_bing:
        candidate = _decode_bing_url(candidate)
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    if decode_bing and parsed.hostname.lower().endswith("bing.com") and parsed.path == "/ck/a":
        # A malformed/unknown wrapper is not the requested destination. Do not
        # expose it as if it were a usable search result.
        return ""
    # Fragments are presentation-only and create duplicate API results.
    return urllib.parse.urlunparse(parsed._replace(fragment=""))


def _parse_bing_serp(html: str, limit: int) -> List[Dict[str, str]]:
    """Parse a Bing SERP into [{title, link, snippet}] (link key matches _scrape_results)."""
    soup = BeautifulSoup(html, "html.parser")
    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a or not a.get("href"):
            continue
        link = _normalize_result_url(
            a["href"],
            base_url="https://www.bing.com/search",
            decode_bing=True,
        )
        if not link or link in seen:
            continue
        seen.add(link)
        cap = li.select_one(".b_caption p") or li.select_one("p")
        out.append({
            "title": a.get_text(" ", strip=True),
            "link": link,
            "snippet": cap.get_text(" ", strip=True) if cap else "",
        })
        if len(out) >= limit:
            break
    return out


def _parse_brave_serp(html: str, limit: int) -> List[Dict[str, str]]:
    """Parse Brave Search's rendered web-result cards.

    Brave changes generated Svelte class suffixes frequently, so the parser
    deliberately anchors on the stable ``data-type=web`` result boundary and
    semantic title/content classes instead of build-specific class names.
    """
    soup = BeautifulSoup(html, "html.parser")
    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    for card in soup.select("div.snippet[data-type='web']"):
        anchor = card.select_one("a[href].l1") or card.select_one("a[href]")
        if not anchor:
            continue
        link = _normalize_result_url(
            anchor.get("href", ""),
            base_url="https://search.brave.com/search",
        )
        if not link or link in seen:
            continue
        seen.add(link)
        title_el = card.select_one(".search-snippet-title") or card.select_one(".title")
        snippet_el = card.select_one(".generic-snippet .content") or card.select_one(
            ".generic-snippet"
        )
        title = title_el.get_text(" ", strip=True) if title_el else anchor.get_text(" ", strip=True)
        if not title:
            continue
        out.append(
            {
                "title": title,
                "link": link,
                "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
            }
        )
        if len(out) >= limit:
            break
    return out


STARSEARCH_SEARCH_ENGINES = {
    "bing": {
        "url": "https://www.bing.com/search?q={query}",
        "parser": _parse_bing_serp,
    },
    "brave": {
        "url": "https://search.brave.com/search?q={query}&source=web",
        "parser": _parse_brave_serp,
    },
}


async def search_web(
    engine: str,
    query: str,
    limit: int = 5,
    retries: int = 3,
    *,
    proxy: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Run one keyless, browser-rendered search engine through StarSearch.

    The engine URL is explicit and never substituted. Transport/navigation
    failures are raised after bounded retries so Huginn can distinguish an
    unhealthy engine from a valid empty result set and update its circuit.
    """
    config = STARSEARCH_SEARCH_ENGINES.get(engine)
    if config is None:
        raise ValueError(f"unsupported StarSearch search engine: {engine}")
    addr = tcp_addr()
    if not addr:
        raise RuntimeError("StarSearch TCP is not configured")
    url = config["url"].format(query=urllib.parse.quote_plus(query))
    last_error: Optional[Exception] = None
    for attempt in range(retries):
        try:
            html, _, egress = await _fetch_page(addr, url, proxy=proxy)
            if not html:
                raise RuntimeError(f"{engine} returned an empty rendered document")
            return [{**result, "_egress": egress} for result in config["parser"](html, limit)]
        except Exception as exc:
            last_error = exc
            if attempt == retries - 1:
                break
            await asyncio.sleep(0.4 * (attempt + 1))
    assert last_error is not None
    raise last_error


async def search_bing(
    query: str,
    limit: int = 5,
    retries: int = 3,
    *,
    proxy: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Compatibility wrapper for StarSearch-rendered Bing search."""
    return await search_web("bing", query, limit, retries, proxy=proxy)


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
