"""Unit tests for huginn/mapper.py page-link extraction via StarSearch (G5a).

_extract_page_links used to drive raw Playwright directly. In live
strict-StarSearch production mode (HUGINN_BROWSER_BACKEND=starsearch,
HUGINN_ALLOW_PLAYWRIGHT_FALLBACK=false) that raised, and the function's
broad except-Exception swallowed it, silently returning zero links for
/v1/map and /v1/research's sitemap step. These tests exercise the
StarSearch-routed replacement (via a faked Scraper, mirroring
huginn/tests/test_crawler.py's FakeScraper pattern) and confirm the
fail-soft-to-empty behavior is preserved for every failure mode.
"""
import pytest

from huginn.mapper import Mapper
from huginn.models import ScrapeData


class FakeScraper:
    """Fake Scraper that returns canned ScrapeData per URL (mirrors test_crawler.py)."""

    def __init__(self, pages: dict):
        self.pages = pages  # url -> ScrapeData

    async def scrape(self, url, **kwargs):
        return self.pages.get(url, ScrapeData(metadata={"url": url, "status_code": 200}))


class NoneScraper:
    """Simulates StarSearch/Scraper returning None (unavailable/blocked page)."""

    async def scrape(self, url, **kwargs):
        return None


class RaisingScraper:
    """Simulates the exact live failure this fix targets: StarSearch-only
    production mode rejecting a request that would otherwise fall back to
    Playwright."""

    async def scrape(self, url, **kwargs):
        raise RuntimeError(
            "Playwright context requested while StarSearch-only production mode is active"
        )


class FakeBrowser:
    """Minimal fake for Mapper's constructor; the StarSearch path never touches it."""
    backend = "starsearch"


@pytest.fixture
def mapper():
    return Mapper(browser=FakeBrowser())


class TestExtractPageLinksViaStarSearch:
    @pytest.mark.asyncio
    async def test_returns_links_from_starsearch_page_data(self, mapper):
        """Links come off page_data.links (StarSearch), not Playwright DOM eval."""
        mapper.scraper = FakeScraper({
            "https://example.com": ScrapeData(
                metadata={"url": "https://example.com", "title": "Home", "status_code": 200},
                links=[
                    "https://example.com/about",
                    "/contact",  # relative href — must resolve against `url`
                    "https://example.com/about",  # duplicate — set dedupes
                ],
            ),
        })

        links, title, status = await mapper._extract_page_links(
            "https://example.com", include_subdomains=False, base_domain="example.com"
        )

        assert links == {"https://example.com/about", "https://example.com/contact"}
        assert title == "Home"
        assert status == 200

    @pytest.mark.asyncio
    async def test_filters_javascript_hash_mailto_and_non_http(self, mapper):
        """Same filtering the old JS applied: drop javascript:/#/mailto: and non-http(s)."""
        mapper.scraper = FakeScraper({
            "https://example.com": ScrapeData(
                metadata={"url": "https://example.com", "title": "Home", "status_code": 200},
                links=[
                    "javascript:void(0)",
                    "#section",
                    "mailto:test@example.com",
                    "ftp://example.com/file",
                    "https://example.com/page",
                ],
            ),
        })

        links, _title, _status = await mapper._extract_page_links(
            "https://example.com", include_subdomains=False, base_domain="example.com"
        )

        assert links == {"https://example.com/page"}

    @pytest.mark.asyncio
    async def test_scrape_returning_none_fails_soft(self, mapper):
        """StarSearch/Scraper returning None -> empty tuple, no raise."""
        mapper.scraper = NoneScraper()

        result = await mapper._extract_page_links(
            "https://example.com", include_subdomains=False, base_domain="example.com"
        )

        assert result == (set(), None, None)

    @pytest.mark.asyncio
    async def test_scrape_failure_status_fails_soft(self, mapper):
        """A ScrapeData carrying an error status (scrape_failure(...) truthy) -> empty."""
        mapper.scraper = FakeScraper({
            "https://example.com": ScrapeData(
                metadata={"url": "https://example.com", "status_code": 500, "error": "boom"},
            ),
        })

        result = await mapper._extract_page_links(
            "https://example.com", include_subdomains=False, base_domain="example.com"
        )

        assert result == (set(), None, None)

    @pytest.mark.asyncio
    async def test_scraper_raising_fails_soft(self, mapper):
        """The exact live bug: StarSearch-only mode raising must not propagate —
        map_site/map_site_graph must still get a clean empty result."""
        mapper.scraper = RaisingScraper()

        result = await mapper._extract_page_links(
            "https://example.com", include_subdomains=False, base_domain="example.com"
        )

        assert result == (set(), None, None)


class TestMapSiteViaStarSearch:
    @pytest.mark.asyncio
    async def test_map_site_returns_page_links_without_sitemap(self, mapper):
        """map_site finds links via the StarSearch page-extraction path even
        with no sitemap.xml at all — this is the /v1/map + /v1/research bug
        this change fixes (previously: zero links, success:true)."""
        mapper.scraper = FakeScraper({
            "https://example.com": ScrapeData(
                metadata={"url": "https://example.com", "title": "Home", "status_code": 200},
                links=["https://example.com/a", "https://example.com/b"],
            ),
        })

        # sitemap="skip" -> no sitemap.xml fetch at all, page links are the
        # only source, proving they don't come from the sitemap step.
        urls = await mapper.map_site("https://example.com", sitemap="skip")

        assert "https://example.com/a" in urls
        assert "https://example.com/b" in urls

    @pytest.mark.asyncio
    async def test_map_site_fails_soft_when_starsearch_unavailable(self, mapper):
        """If the page render fails, map_site still returns (no exception),
        just with no page-discovered URLs."""
        mapper.scraper = NoneScraper()

        urls = await mapper.map_site("https://example.com", sitemap="skip")

        assert urls == []
