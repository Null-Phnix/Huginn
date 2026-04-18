"""
Huginn Browser Manager

Wraps Playwright (and optionally StarSearch) with stealth patches,
DOM walker integration, and content extraction.
The heart of the system — inherited from Blackreach's battle-tested browser stack.
"""

import asyncio
import base64
import glob
import json
import logging
import re
import time
from enum import Enum
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urljoin, urlparse

from pydantic import BaseModel, Field

from playwright.async_api import async_playwright, Browser, Page, BrowserContext

from .config import BrowserConfig

logger = logging.getLogger(__name__)

# Named constants (from Blackreach hardening)
DEFAULT_NAVIGATION_TIMEOUT = 30000
DEFAULT_RENDER_WAIT = 2000
CHALLENGE_WAIT_SECONDS = 8
MAX_CHALLENGE_RETRIES = 3
STEALTH_INIT_JS = """
// Patch navigator.webdriver
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// Patch chrome runtime
window.chrome = { runtime: {} };

// Patch permissions
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
);

// Patch plugins (add common plugins)
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5]
});

// Patch languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en']
});
"""


class WaitStrategy(str, Enum):
    """Smart wait strategies for page navigation.

    Instead of a dumb asyncio.sleep, we can:
    - TIMEOUT: sleep for N ms (backward compatible)
    - SELECTOR: wait for a CSS selector to appear
    - NETWORK_IDLE: wait for network activity to settle
    - DOM_CONTENT_LOADED: wait for DOM ready (fastest)
    """
    TIMEOUT = "timeout"
    SELECTOR = "selector"
    NETWORK_IDLE = "networkidle"
    DOM_CONTENT_LOADED = "domcontentloaded"


def parse_wait_for(wait_for: Optional[Union[int, float, str]]) -> Tuple[WaitStrategy, Union[int, str]]:
    """Parse the wait_for parameter into a (WaitStrategy, value) tuple.

    Accepts:
        int/float — timeout in ms: (TIMEOUT, int_ms)
        "css.selector" — wait for element: (SELECTOR, "css.selector")
        "networkidle" / "network-idle" / "network_idle" — wait for network idle: (NETWORK_IDLE, 5000)
        "domcontentloaded" — wait for DOM ready: (DOM_CONTENT_LOADED, 0)
        None — default timeout: (TIMEOUT, 3000)
    """
    if wait_for is None:
        return (WaitStrategy.TIMEOUT, 3000)
    if isinstance(wait_for, (int, float)):
        return (WaitStrategy.TIMEOUT, int(wait_for))
    if isinstance(wait_for, str):
        if wait_for in ("networkidle", "network_idle", "network-idle"):
            return (WaitStrategy.NETWORK_IDLE, 5000)
        if wait_for == "domcontentloaded":
            return (WaitStrategy.DOM_CONTENT_LOADED, 0)
        # Otherwise treat as CSS selector
        return (WaitStrategy.SELECTOR, wait_for)
    return (WaitStrategy.TIMEOUT, 3000)


class ScrollConfig(BaseModel):
    """Configuration for infinite scroll handling.

    Controls how auto_scroll iterates through dynamically-loaded content
    (infinite scroll pages, lazy-loaded images, load-more buttons).
    """
    max_scrolls: int = Field(default=10, ge=1, description="Maximum number of scroll iterations")
    delay_ms: int = Field(default=500, ge=0, description="Delay in ms between scrolls for content to load")
    scroll_to_bottom: bool = Field(default=True, description="Scroll back to top after finishing")


class StarSearchBackend:
    """Communicates with the StarSearch daemon via Unix socket.

    The StarSearch daemon provides stealth-first browsing powered by
    Rust and 80+ browser fingerprints.  It speaks a simple JSON-over-
    Unix-socket protocol: each command is a JSON object terminated by
    a newline, and each response is likewise.

    Commands
    --------
    {"cmd": "navigate",   "url": "<url>"}            -> {"ok": bool, "status": int}
    {"cmd": "content"}                                 -> {"ok": bool, "title": str, "text": str, ...}
    {"cmd": "links"}                                    -> {"ok": bool, "links": [str, ...]}
    {"cmd": "screenshot", "full_page": bool}            -> {"ok": bool, "data": "<base64>"}
    {"cmd": "shutdown"}                                 -> {"ok": bool}
    """

    def __init__(self, socket_path: Optional[str] = None):
        self.socket_path = socket_path
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False

    # ── socket discovery ─────────────────────────────────────────────────

    @staticmethod
    def _discover_socket() -> Optional[str]:
        """Find the StarSearch daemon socket under /tmp/starsearch-*/daemon.sock."""
        pattern = "/tmp/starsearch-*/daemon.sock"
        matches = sorted(glob.glob(pattern))
        if matches:
            logger.info(f"StarSearch daemon socket found: {matches[0]}")
            return matches[0]
        logger.warning("No StarSearch daemon socket found")
        return None

    # ── lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> bool:
        """Connect to the StarSearch daemon.  Returns True on success."""
        path = self.socket_path or self._discover_socket()
        if path:
            self.socket_path = path
        else:
            logger.warning("StarSearch: no socket path available, cannot start")
            return False

        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.socket_path),
                timeout=5.0,
            )
            self._connected = True
            logger.info(f"StarSearch backend connected to {self.socket_path}")
            return True
        except Exception as exc:
            logger.warning(f"StarSearch: failed to connect to {self.socket_path}: {exc}")
            self._connected = False
            return False

    async def stop(self):
        """Tell the daemon we're done, then close the connection."""
        if self._connected:
            try:
                await self._send_command({"cmd": "shutdown"})
            except Exception:
                pass
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None
        self._connected = False

    # ── low-level protocol ──────────────────────────────────────────────

    async def _send_command(self, command: Dict[str, Any]) -> Dict[str, Any]:
        """Send a JSON command and read the JSON response."""
        if not self._connected or self._writer is None or self._reader is None:
            raise RuntimeError("StarSearch backend is not connected")

        payload = json.dumps(command) + "\n"
        self._writer.write(payload.encode("utf-8"))
        await self._writer.drain()

        # Read response line
        data = await asyncio.wait_for(self._reader.readline(), timeout=60.0)
        if not data:
            raise RuntimeError("StarSearch daemon closed the connection")
        return json.loads(data.decode("utf-8").strip())

    # ── high-level API ───────────────────────────────────────────────────

    async def navigate(self, url: str) -> bool:
        """Navigate the daemon to *url*.  Returns True on success."""
        resp = await self._send_command({"cmd": "navigate", "url": url})
        return resp.get("ok", False)

    async def extract_content(self) -> Dict[str, Any]:
        """Return the structured page content from the daemon."""
        resp = await self._send_command({"cmd": "content"})
        if not resp.get("ok", False):
            raise RuntimeError("StarSearch content extraction failed")
        return resp

    async def get_links(self, base_url: Optional[str] = None) -> List[str]:
        """Return discovered links, optionally filtered to *base_url*'s domain."""
        resp = await self._send_command({"cmd": "links"})
        if not resp.get("ok", False):
            raise RuntimeError("StarSearch link discovery failed")
        links: List[str] = resp.get("links", [])
        if base_url:
            domain = urlparse(base_url).netloc
            links = [l for l in links if urlparse(l).netloc == domain]
        return links

    async def take_screenshot(self, full_page: bool = True) -> str:
        """Return a base64-encoded screenshot."""
        resp = await self._send_command({"cmd": "screenshot", "full_page": full_page})
        if not resp.get("ok", False):
            raise RuntimeError("StarSearch screenshot failed")
        return resp.get("data", "")


class BrowserManager:
    """Manages Playwright browser instances with stealth and content extraction.

    When *config* is supplied and ``config.backend == 'starsearch'``, the
    manager delegates to a :class:`StarSearchBackend` instead of Playwright.
    If the StarSearch daemon is unavailable it falls back to Playwright with
    a warning.

    When *config* is ``None`` (the default) the manager behaves exactly as
    before — pure Playwright — preserving full backward compatibility.
    """

    def __init__(self, headless: bool = True, stealth: bool = True,
                 navigation_timeout: int = DEFAULT_NAVIGATION_TIMEOUT,
                 viewport: Tuple[int, int] = (1920, 1080),
                 user_agent: Optional[str] = None,
                 config: Optional[BrowserConfig] = None):
        self.headless = headless
        self.stealth = stealth
        self.navigation_timeout = navigation_timeout
        self.viewport = viewport
        self.user_agent = user_agent

        # Decide backend
        self._backend_name: str = "playwright"
        self._starsearch: Optional[StarSearchBackend] = None

        if config is not None:
            self._backend_name = config.backend
            # Override scalar settings from config when they differ from defaults
            if config.headless != headless:
                self.headless = config.headless
            if config.stealth_mode != stealth:
                self.stealth = config.stealth_mode
            if config.navigation_timeout != navigation_timeout:
                self.navigation_timeout = config.navigation_timeout
            if config.viewport_width and config.viewport_height:
                self.viewport = (config.viewport_width, config.viewport_height)
            if config.user_agent:
                self.user_agent = config.user_agent

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._contexts: List[BrowserContext] = []
        self.last_status_code: int = 0

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def start(self):
        """Launch browser with stealth configuration."""
        # If StarSearch is requested, try to connect first
        if self._backend_name == "starsearch":
            self._starsearch = StarSearchBackend()
            connected = await self._starsearch.start()
            if connected:
                logger.info("BrowserManager: using StarSearch backend")
                return
            else:
                logger.warning(
                    "StarSearch daemon unavailable — falling back to Playwright"
                )
                self._starsearch = None
                self._backend_name = "playwright"

        # Playwright path (default or fallback)
        if self._browser:
            return

        self._playwright = await async_playwright().start()
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ]

        try:
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=launch_args,
            )
        except Exception as e:
            logger.error(f"Failed to launch browser: {e}")
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
            raise
        logger.info(f"Browser launched (headless={self.headless}, stealth={self.stealth})")

    async def stop(self):
        """Shut down browser and clean up."""
        if self._starsearch:
            await self._starsearch.stop()
            self._starsearch = None
            return

        for ctx in self._contexts:
            try:
                await ctx.close()
            except Exception:
                pass
        self._contexts.clear()
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    # ── context / page creation ───────────────────────────────────────────

    async def new_context(self, proxy: Optional[Dict[str, str]] = None) -> BrowserContext:
        """Create a new browser context with stealth config."""
        if not self._browser:
            await self.start()

        context_kwargs = {
            "viewport": {"width": self.viewport[0], "height": self.viewport[1]},
            "user_agent": self.user_agent,
            "java_script_enabled": True,
            "ignore_https_errors": True,
        }
        if proxy:
            context_kwargs["proxy"] = proxy

        context = await self._browser.new_context(**context_kwargs)
        context.set_default_navigation_timeout(self.navigation_timeout)
        context.set_default_timeout(self.navigation_timeout)

        if self.stealth:
            await context.add_init_script(STEALTH_INIT_JS)

        self._contexts.append(context)
        return context

    async def new_page(self, context: Optional[BrowserContext] = None) -> Page:
        """Create a new page with stealth patches applied."""
        if context is None:
            context = await self.new_context()
        page = await context.new_page()

        # Additional stealth patches on page level
        if self.stealth:
            await page.add_init_script(STEALTH_INIT_JS)

        return page

    # ── navigation ────────────────────────────────────────────────────────

    async def navigate(self, page: Page, url: str, wait_until: str = "domcontentloaded") -> bool:
        """Navigate to URL with challenge detection and handling."""
        # StarSearch path — uses its own browsing engine
        if self._starsearch:
            return await self._starsearch.navigate(url)

        # Playwright path
        self.last_status_code = 0
        try:
            response = await page.goto(url, wait_until=wait_until)
            if not response:
                logger.warning(f"No response for {url}")
                return False

            self.last_status_code = response.status

            # Handle challenge pages (Cloudflare, etc.)
            if await self._is_challenge_page(page):
                await self._handle_challenge(page)

            return response.ok
        except Exception as e:
            logger.error(f"Navigation failed for {url}: {e}")
            self.last_status_code = 0
            return False

    async def smart_wait(self, page: Page, strategy: WaitStrategy, value, timeout_ms: int = 10000):
        """Apply intelligent wait strategy to the page.

        Args:
            page: Playwright page object
            strategy: WaitStrategy enum value
            value: Timeout in ms (TIMEOUT), selector string (SELECTOR), or unused
            timeout_ms: Max time to wait for selector/networkidle
        """
        if strategy == WaitStrategy.TIMEOUT:
            await asyncio.sleep(value / 1000)
        elif strategy == WaitStrategy.SELECTOR:
            try:
                await page.wait_for_selector(value, timeout=timeout_ms)
            except Exception as e:
                logger.warning(f"Selector wait failed for '{value}': {e}")
        elif strategy == WaitStrategy.NETWORK_IDLE:
            try:
                await page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except Exception:
                pass  # networkidle can timeout on complex pages, that's ok
        elif strategy == WaitStrategy.DOM_CONTENT_LOADED:
            try:
                await page.wait_for_load_state("domcontentloaded")
            except Exception:
                pass  # already loaded

    # ── infinite scroll ──────────────────────────────────────────────────────

    async def auto_scroll(self, page: Page, config: Optional[ScrollConfig] = None) -> int:
        """Auto-scroll a page to load lazy/dynamic content.

        Scrolls down repeatedly, waiting for new content to load between
        each scroll. Stops when the page height stops changing or max_scrolls
        is reached. Optionally scrolls back to the top when done.

        Args:
            page: Playwright page object
            config: ScrollConfig controlling behavior (uses defaults if None)

        Returns:
            Number of scrolls performed.
        """
        if config is None:
            config = ScrollConfig()

        scroll_count = 0
        last_height = 0

        for _ in range(config.max_scrolls):
            current_height = await page.evaluate("document.body.scrollHeight")
            if current_height == last_height and scroll_count > 0:
                break  # No more content loading

            last_height = current_height

            # Scroll to bottom
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            scroll_count += 1

            # Wait for new content to load
            await asyncio.sleep(config.delay_ms / 1000)

        # Scroll back to top if requested
        if config.scroll_to_bottom:
            await page.evaluate("window.scrollTo(0, 0)")

        return scroll_count

    # ── content extraction ─────────────────────────────────────────────────

    async def extract_content(self, page: Page) -> Dict[str, Any]:
        """
        Extract page content using the DOM walker approach.
        Returns structured data: text, links, interactive elements, metadata.
        """
        # StarSearch path
        if self._starsearch:
            return await self._starsearch.extract_content()

        # Playwright path
        # Wait for content to render
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass  # networkidle can timeout on complex pages, that's ok

        # Run DOM walker extraction
        result = await page.evaluate("""() => {
            const output = {
                title: document.title,
                url: window.location.href,
                description: '',
                language: document.documentElement.lang || 'en',
                links: [],
                interactive: [],
                text: '',
                headings: [],
                meta: {}
            };

            // Meta description
            const metaDesc = document.querySelector('meta[name="description"]');
            if (metaDesc) output.description = metaDesc.content || '';

            // Meta tags
            document.querySelectorAll('meta').forEach(m => {
                const name = m.getAttribute('name') || m.getAttribute('property') || m.getAttribute('http-equiv');
                const content = m.getAttribute('content');
                if (name && content) output.meta[name] = content;
            });

            // Extract links
            document.querySelectorAll('a[href]').forEach(a => {
                const href = a.href;
                if (href && !href.startsWith('javascript:')) {
                    output.links.push({
                        href: href,
                        text: (a.textContent || '').trim().substring(0, 200),
                        rel: a.getAttribute('rel') || '',
                    });
                }
            });

            // Extract headings for structure
            document.querySelectorAll('h1, h2, h3, h4, h5, h6').forEach(h => {
                output.headings.push({
                    level: parseInt(h.tagName[1]),
                    text: (h.textContent || '').trim().substring(0, 500),
                    id: h.id || ''
                });
            });

            // Extract main content
            // Try <main>, <article>, then fall back to body
            let mainEl = document.querySelector('main') ||
                         document.querySelector('article') ||
                         document.querySelector('[role="main"]');

            if (!mainEl) {
                // Try largest content block heuristic
                const candidates = document.querySelectorAll('div, section');
                let maxSize = 0;
                candidates.forEach(el => {
                    const textLen = (el.textContent || '').length;
                    if (textLen > maxSize && textLen < document.body.textContent.length * 0.95) {
                        maxSize = textLen;
                        mainEl = el;
                    }
                });
            }

            const contentEl = mainEl || document.body;

            // Clean content: remove scripts, styles, nav, footer, ads
            const clone = contentEl.cloneNode(true);
            const removeSelectors = [
                'script', 'style', 'noscript', 'nav', 'header', 'footer',
                '.nav', '.header', '.footer', '.sidebar', '.ad', '.ads',
                '.cookie', '.popup', '.modal', '.overlay', '.banner',
                '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
                '.social-share', '.related-posts', '.comments'
            ];
            removeSelectors.forEach(sel => {
                try {
                    clone.querySelectorAll(sel).forEach(el => el.remove());
                } catch(e) {}
            });

            // Get cleaned text
            output.text = (clone.textContent || '').replace(/\\s+/g, ' ').trim();

            // Remove links that are inside removed elements
            output.links = output.links.filter(l => {
                try {
                    return document.body.contains(document.querySelector(`a[href="${l.href}"]`));
                } catch { return true; }
            });

            return output;
        }""")

        return result

    async def to_markdown(self, page: Page, content: Optional[Dict] = None) -> str:
        """
        Convert page content to clean Markdown.
        Uses a JS-based HTML-to-markdown approach for maximum fidelity.
        """
        # StarSearch doesn't have a separate markdown endpoint —
        # we build it from extract_content().  The Playwright path
        # uses its own JS evaluator, which produces richer output.
        if self._starsearch:
            if content is None:
                content = await self.extract_content(page)
            # Lightweight markdown from structured content
            md_parts = []
            if content.get("title"):
                md_parts.append(f"# {content['title']}\n")
            if content.get("description"):
                md_parts.append(f"> {content['description']}\n")
            if content.get("text"):
                md_parts.append(content["text"])
            return "\n".join(md_parts)

        if content is None:
            content = await self.extract_content(page)

        md_parts = []

        # Title
        if content.get("title"):
            md_parts.append(f"# {content['title']}\n")

        # Meta description
        if content.get("description"):
            md_parts.append(f"> {content['description']}\n")

        # Headings and content
        md_text = await page.evaluate("""() => {
            const clone = (document.querySelector('main') ||
                          document.querySelector('article') ||
                          document.querySelector('[role="main"]') ||
                          document.body).cloneNode(true);

            // Remove noise
            const removeSelectors = [
                'script', 'style', 'noscript', 'nav', 'footer',
                '.nav', '.header', '.footer', '.sidebar', '.ad',
                '.cookie', '.popup', '.modal', '.overlay',
                '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
                '.social-share', '.related-posts', '.comments'
            ];
            removeSelectors.forEach(sel => {
                try { clone.querySelectorAll(sel).forEach(el => el.remove()); } catch(e) {}
            });

            // Convert HTML to simple markdown
            function htmlToMd(el) {
                let result = '';
                for (const node of el.childNodes) {
                    if (node.nodeType === 3) {  // Text
                        result += node.textContent;
                    } else if (node.nodeType === 1) {  // Element
                        const tag = node.tagName.toLowerCase();
                        const inner = htmlToMd(node);

                        switch(tag) {
                            case 'h1': result += `# ${inner.trim()}\\n\\n`; break;
                            case 'h2': result += `## ${inner.trim()}\\n\\n`; break;
                            case 'h3': result += `### ${inner.trim()}\\n\\n`; break;
                            case 'h4': result += `#### ${inner.trim()}\\n\\n`; break;
                            case 'h5': result += `##### ${inner.trim()}\\n\\n`; break;
                            case 'h6': result += `###### ${inner.trim()}\\n\\n`; break;
                            case 'p': result += `${inner.trim()}\\n\\n`; break;
                            case 'a':
                                const href = node.getAttribute('href') || '';
                                result += href ? `[${inner.trim()}](${href})` : inner;
                                break;
                            case 'strong': case 'b': result += `**${inner.trim()}**`; break;
                            case 'em': case 'i': result += `*${inner.trim()}*`; break;
                            case 'code': result += `\\`${inner.trim()}\\``; break;
                            case 'pre': result += `\\`\\`\\`\\n${inner.trim()}\\n\\`\\`\\`\\n\\n`; break;
                            case 'blockquote': result += `> ${inner.trim()}\\n\\n`; break;
                            case 'ul': case 'ol': result += `${inner}\\n`; break;
                            case 'li': result += `- ${inner.trim()}\\n`; break;
                            case 'br': result += '\\n'; break;
                            case 'hr': result += '---\\n\\n'; break;
                            case 'img':
                                const src = node.getAttribute('src') || '';
                                const alt = node.getAttribute('alt') || '';
                                result += `![${alt}](${src})`;
                                break;
                            case 'table': result += inner + '\\n'; break;
                            case 'tr':
                                const cells = [...node.querySelectorAll('td, th')].map(c => c.textContent.trim());
                                result += `| ${cells.join(' | ')} |\\n`;
                                if (node.querySelector('th')) {
                                    result += `| ${cells.map(() => '---').join(' | ')} |\\n`;
                                }
                                break;
                            case 'th': case 'td': break;  // handled by tr
                            case 'figure': result += `${inner}\\n`; break;
                            case 'figcaption': result += `*${inner.trim()}*\\n`; break;
                            case 'div': case 'section': case 'article': result += `${inner}\\n`; break;
                            case 'span': result += inner; break;
                            default: result += inner; break;
                        }
                    }
                }
                return result;
            }

            return htmlToMd(clone);
        }""")

        # Clean up excessive whitespace
        md_text = re.sub(r'\n{3,}', '\n\n', md_text)
        md_text = md_text.strip()

        if md_text:
            md_parts.append(md_text)

        return '\n'.join(md_parts)

    async def to_html(self, page: Page, only_main: bool = True) -> str:
        """Extract page HTML, optionally just main content."""
        # StarSearch path: use content from extract_content
        if self._starsearch:
            content = await self._starsearch.extract_content()
            # The daemon returns structured data, not raw HTML.
            # When using StarSearch we can only return the text content.
            # For real HTML, the caller should use Playwright.
            return content.get("html", content.get("text", ""))

        if only_main:
            html = await page.evaluate("""() => {
                const main = document.querySelector('main') ||
                             document.querySelector('article') ||
                             document.querySelector('[role="main"]') ||
                             document.body;
                return main ? main.innerHTML : '';
            }""")
        else:
            html = await page.content()
        return html

    async def get_links(self, page: Page, base_url: Optional[str] = None) -> List[str]:
        """Extract all links from page, resolving relative URLs."""
        # StarSearch path
        if self._starsearch:
            return await self._starsearch.get_links(base_url)

        # Playwright path
        raw_links = await page.evaluate("""() => {
            const links = [];
            document.querySelectorAll('a[href]').forEach(a => {
                const href = a.href;
                if (href && !href.startsWith('javascript:') && !href.startsWith('#')) {
                    links.push(href);
                }
            });
            return [...new Set(links)];
        }""")

        if base_url:
            # Filter to same domain if base_url provided
            parsed = urlparse(base_url)
            domain = parsed.netloc
            raw_links = [l for l in raw_links if urlparse(l).netloc == domain]

        return raw_links

    async def take_screenshot(self, page: Page, full_page: bool = True) -> str:
        """Take screenshot and return as base64 string."""
        # StarSearch path
        if self._starsearch:
            return await self._starsearch.take_screenshot(full_page=full_page)

        # Playwright path
        buf = await page.screenshot(full_page=full_page)
        return base64.b64encode(buf).decode("utf-8")

    async def _is_challenge_page(self, page: Page) -> bool:
        """Detect if page is a challenge/CAPTCHA interstitial."""
        try:
            content = (await page.title() + " " + await page.evaluate(
                "() => (document.body || {}).textContent || ''"
            )).lower()

            challenge_indicators = [
                "checking your browser",
                "cloudflare",
                "please wait",
                "verify you are human",
                "are you a robot",
                "just a moment",
                "ddos protection",
            ]
            return any(ind in content for ind in challenge_indicators)
        except Exception:
            return False

    async def _handle_challenge(self, page: Page):
        """Wait for challenge page to resolve automatically."""
        logger.info("Challenge page detected, waiting for resolution...")
        for i in range(MAX_CHALLENGE_RETRIES):
            await asyncio.sleep(CHALLENGE_WAIT_SECONDS)
            if not await self._is_challenge_page(page):
                logger.info("Challenge resolved after waiting")
                return
        logger.warning("Challenge not resolved after max retries")

    async def execute_actions(self, page: Page, actions: List[dict]):
        """Execute a sequence of browser actions."""
        # StarSearch doesn't support composite actions yet —
        # fall through to Playwright (actions require a Page object anyway)
        for action in actions:
            action_type = action.get("type", "")
            try:
                if action_type == "click":
                    selector = action.get("selector", "")
                    if selector:
                        await page.click(selector)
                elif action_type == "wait":
                    ms = action.get("amount", 1000)
                    await asyncio.sleep(ms / 1000)
                elif action_type == "scroll":
                    direction = action.get("direction", "down")
                    try:
                        amount = int(action.get("amount", 500))
                    except (ValueError, TypeError):
                        logger.warning(f"Invalid scroll amount: {action.get('amount')}")
                        amount = 500
                    if direction == "down":
                        await page.evaluate(f"window.scrollBy(0, {amount})")
                    else:
                        await page.evaluate(f"window.scrollBy(0, -{amount})")
                elif action_type == "screenshot":
                    pass  # screenshots handled in result collection
                elif action_type == "type":
                    selector = action.get("selector", "")
                    text = action.get("text", "")
                    if selector and text:
                        await page.fill(selector, text)
                elif action_type == "press":
                    key = action.get("key", "Enter")
                    await page.keyboard.press(key)
                else:
                    logger.warning(f"Unknown action type: {action_type}")
            except Exception as e:
                logger.warning(f"Action failed: {action_type} - {e}")

    @property
    def backend(self) -> str:
        """Return the name of the active backend (``'playwright'`` or ``'starsearch'``)."""
        if self._starsearch is not None:
            return "starsearch"
        return self._backend_name
