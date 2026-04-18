"""
BlackCrawl Browser Manager

Wraps Playwright (and optionally StarSearch) with stealth patches,
DOM walker integration, and content extraction.
The heart of the system — inherited from Blackreach's battle-tested browser stack.
"""

import asyncio
import base64
import logging
import re
import time
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright, Browser, Page, BrowserContext

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


class BrowserManager:
    """Manages Playwright browser instances with stealth and content extraction."""

    def __init__(self, headless: bool = True, stealth: bool = True,
                 navigation_timeout: int = DEFAULT_NAVIGATION_TIMEOUT,
                 viewport: Tuple[int, int] = (1920, 1080),
                 user_agent: Optional[str] = None):
        self.headless = headless
        self.stealth = stealth
        self.navigation_timeout = navigation_timeout
        self.viewport = viewport
        self.user_agent = user_agent
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._contexts: List[BrowserContext] = []

    async def start(self):
        """Launch browser with stealth configuration."""
        if self._browser:
            return

        self._playwright = await async_playwright().start()
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ]

        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=launch_args,
        )
        logger.info(f"Browser launched (headless={self.headless}, stealth={self.stealth})")

    async def stop(self):
        """Shut down browser and clean up."""
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

    async def new_context(self) -> BrowserContext:
        """Create a new browser context with stealth config."""
        if not self._browser:
            await self.start()

        context = await self._browser.new_context(
            viewport={"width": self.viewport[0], "height": self.viewport[1]},
            user_agent=self.user_agent,
            java_script_enabled=True,
            ignore_https_errors=True,
        )
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

    async def navigate(self, page: Page, url: str, wait_until: str = "domcontentloaded") -> bool:
        """Navigate to URL with challenge detection and handling."""
        try:
            response = await page.goto(url, wait_until=wait_until)
            if not response:
                logger.warning(f"No response for {url}")
                return False

            # Handle challenge pages (Cloudflare, etc.)
            if await self._is_challenge_page(page):
                await self._handle_challenge(page)

            return response.ok
        except Exception as e:
            logger.error(f"Navigation failed for {url}: {e}")
            return False

    async def extract_content(self, page: Page) -> Dict[str, Any]:
        """
        Extract page content using the DOM walker approach.
        Returns structured data: text, links, interactive elements, metadata.
        """
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
                "checking your browser",
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
                    amount = action.get("amount", 500)
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