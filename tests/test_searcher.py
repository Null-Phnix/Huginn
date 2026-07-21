"""
Tests for Huginn Searcher — Search engine configuration, result parsing.
"""

import pytest

from huginn import starsearch_scrape
from huginn.proxy import ProxyEndpoint, StaticProxyProvider
from huginn.searcher import (
    SearchEngineHealthRegistry,
    Searcher,
)


class TestSearchEngineConfig:
    """All three production engines are explicit StarSearch-rendered destinations."""

    def test_all_engines_have_url_and_parser(self):
        for name, config in starsearch_scrape.STARSEARCH_SEARCH_ENGINES.items():
            assert config["url"].startswith("https://")
            assert callable(config["parser"]), f"{name} missing parser"

    def test_engine_names(self):
        assert set(starsearch_scrape.STARSEARCH_SEARCH_ENGINES) == {"bing", "brave", "duckduckgo"}

    def test_engine_urls_contain_query_placeholder(self):
        for name, config in starsearch_scrape.STARSEARCH_SEARCH_ENGINES.items():
            assert "{query}" in config["url"], f"{name} URL missing {{query}} placeholder"

    def test_bing_url_format(self):
        url = starsearch_scrape.STARSEARCH_SEARCH_ENGINES["bing"]["url"].format(query="test+search")
        assert "bing.com" in url
        assert "test+search" in url

    def test_brave_url_format(self):
        url = starsearch_scrape.STARSEARCH_SEARCH_ENGINES["brave"]["url"].format(query="test+search")
        assert "search.brave.com" in url
        assert "test+search" in url

    def test_duckduckgo_url_format(self):
        url = starsearch_scrape.STARSEARCH_SEARCH_ENGINES["duckduckgo"]["url"].format(query="test+search")
        assert "html.duckduckgo.com" in url
        assert "test+search" in url


class TestSearcherInit:
    """Test Searcher initialization."""

    def test_default_fallback_chain(self):
        searcher = Searcher(browser=None)
        assert searcher.fallback_chain is True

    def test_custom_fallback_chain(self):
        searcher = Searcher(browser=None, fallback_chain=False)
        assert searcher.fallback_chain is False


class TestRenderedSerpParsing:
    def test_bing_parser_decodes_wrapper_and_drops_unsafe_links(self):
        html = """
        <ol>
          <li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9kb2Nz">Example docs</a></h2><div class="b_caption"><p>Useful docs.</p></div></li>
          <li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u=unknown-wrapper">Tracking only</a></h2></li>
          <li class="b_algo"><h2><a href="javascript:alert(1)">Unsafe</a></h2></li>
        </ol>
        """
        assert starsearch_scrape._parse_bing_serp(html, 5) == [
            {"title": "Example docs", "link": "https://example.com/docs", "snippet": "Useful docs."}
        ]

    def test_brave_parser_uses_web_cards_and_absolute_destinations(self):
        html = """
        <main id="results">
          <div class="snippet generated" data-type="web">
            <div class="result-content">
              <a class="l1" href="https://prometheus.io/#home">
                <div class="title search-snippet-title">Prometheus monitoring</div>
              </a>
              <div class="generic-snippet"><div class="content">Open source monitoring.</div></div>
            </div>
          </div>
          <div class="snippet" data-type="news"><a class="l1" href="https://news.example/">News</a></div>
          <div class="snippet" data-type="web"><a class="l1" href="data:text/html,bad"><div class="title">Bad</div></a></div>
        </main>
        """
        assert starsearch_scrape._parse_brave_serp(html, 5) == [
            {
                "title": "Prometheus monitoring",
                "link": "https://prometheus.io/",
                "snippet": "Open source monitoring.",
            }
        ]

    def test_ddg_parser_unwraps_redirect_and_extracts_results(self):
        # Trimmed, grounded fixture captured from a live render of
        # https://html.duckduckgo.com/html/?q=quantum+entanglement
        html = """
        <div id="links" class="results">
          <div class="result results_links results_links_deep web-result ">
            <div class="links_main links_deep result__body">
              <h2 class="result__title">
                <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FQuantum_entanglement&amp;rut=75af6e0df54f5015d600e2575cfe153e1e47c983777d2a6fa0041bb47906a833">Quantum entanglement - Wikipedia</a>
              </h2>
              <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FQuantum_entanglement&amp;rut=75af6e0df54f5015d600e2575cfe153e1e47c983777d2a6fa0041bb47906a833">Quantum entanglement is the phenomenon in which the quantum state of each particle in a group cannot be described independently of the state of the others.</a>
            </div>
          </div>
          <div class="result results_links results_links_deep web-result ">
            <div class="links_main links_deep result__body">
              <h2 class="result__title">
                <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fscience.nasa.gov%2Fwhat%2Dis%2Dthe%2Dspooky%2Dscience%2Dof%2Dquantum%2Dentanglement%2F&amp;rut=5fbdc5a0b051cf5cd8062b3fea79ad85d1a4172db450af845fce9b8207f6366b">What is Quantum Entanglement? NASA Science</a>
              </h2>
              <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fscience.nasa.gov%2Fwhat%2Dis%2Dthe%2Dspooky%2Dscience%2Dof%2Dquantum%2Dentanglement%2F&amp;rut=5fbdc5a0b051cf5cd8062b3fea79ad85d1a4172db450af845fce9b8207f6366b">Learn how particles of the same origin are always connected, even if they are far apart, through the phenomenon of quantum entanglement.</a>
            </div>
          </div>
        </div>
        """
        assert starsearch_scrape._parse_ddg_serp(html, 5) == [
            {
                "title": "Quantum entanglement - Wikipedia",
                "link": "https://en.wikipedia.org/wiki/Quantum_entanglement",
                "snippet": (
                    "Quantum entanglement is the phenomenon in which the quantum "
                    "state of each particle in a group cannot be described "
                    "independently of the state of the others."
                ),
            },
            {
                "title": "What is Quantum Entanglement? NASA Science",
                "link": "https://science.nasa.gov/what-is-the-spooky-science-of-quantum-entanglement/",
                "snippet": (
                    "Learn how particles of the same origin are always connected, "
                    "even if they are far apart, through the phenomenon of quantum "
                    "entanglement."
                ),
            },
        ]

    def test_ddg_parser_skips_undecodable_links_and_dedupes(self):
        html = """
        <div id="links" class="results">
          <div class="result results_links results_links_deep web-result ">
            <div class="links_main links_deep result__body">
              <h2 class="result__title">
                <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs&amp;rut=abc">Example docs</a>
              </h2>
              <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs&amp;rut=abc">Useful docs.</a>
            </div>
          </div>
          <div class="result results_links results_links_deep web-result ">
            <div class="links_main links_deep result__body">
              <h2 class="result__title">
                <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs&amp;rut=xyz">Duplicate docs</a>
              </h2>
            </div>
          </div>
          <div class="result results_links results_links_deep web-result ">
            <div class="links_main links_deep result__body">
              <h2 class="result__title">
                <a rel="nofollow" class="result__a" href="javascript:alert(1)">Unsafe</a>
              </h2>
            </div>
          </div>
          <div class="result results_links results_links_deep web-result ">
            <div class="links_main links_deep result__body">
              <h2 class="result__title">
                <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?rut=no-target">No uddg param</a>
              </h2>
            </div>
          </div>
        </div>
        """
        assert starsearch_scrape._parse_ddg_serp(html, 5) == [
            {"title": "Example docs", "link": "https://example.com/docs", "snippet": "Useful docs."}
        ]


class TestDecodeDdgUrl:
    def test_wrapped_href_decodes_to_real_target(self):
        href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs&rut=abc123"
        assert starsearch_scrape._decode_ddg_url(href) == "https://example.com/docs"

    def test_bare_http_href_passes_through(self):
        assert starsearch_scrape._decode_ddg_url("https://example.com/docs") == "https://example.com/docs"

    def test_junk_href_returns_empty_string(self):
        assert starsearch_scrape._decode_ddg_url("javascript:alert(1)") == ""
        assert starsearch_scrape._decode_ddg_url("//duckduckgo.com/l/?rut=no-target") == ""

    def test_target_percent_octets_and_plus_are_not_double_decoded(self):
        # DDG double-encodes percent-octets in the target (%20 -> %2520). The
        # decoder must unquote exactly once, preserving the target's own
        # encoding, and must not apply form '+' -> space translation.
        href = (
            "//duckduckgo.com/l/?uddg="
            "https%3A%2F%2Fexample.com%2Fpath%2520with%2520space%3Fq%3Da+b&rut=x"
        )
        assert (
            starsearch_scrape._decode_ddg_url(href)
            == "https://example.com/path%20with%20space?q=a+b"
        )


class TestSearchEngineHealth:
    def test_score_reorders_after_failure(self):
        health = SearchEngineHealthRegistry(failure_threshold=2, cooldown_seconds=30)
        ordered, opened = health.select()
        assert ordered == ["bing", "brave", "duckduckgo"]
        assert opened == []

        health.record_failure("bing", 2000, "timeout")
        ordered, _ = health.select()
        assert ordered[0] == "brave"
        assert health.snapshot()["bing"].status == "degraded"

    def test_repeated_failures_open_circuit(self):
        health = SearchEngineHealthRegistry(failure_threshold=2, cooldown_seconds=30)
        health.record_failure("bing", 100, "timeout")
        health.record_failure("bing", 100, "timeout")
        ordered, opened = health.select()
        assert ordered == ["brave", "duckduckgo"]
        assert opened == ["bing"]
        assert health.snapshot()["bing"].status == "open"


@pytest.mark.asyncio
async def test_search_falls_back_to_independent_rendered_engine(monkeypatch):
    calls = []

    async def fake_search(engine, query, limit, *, proxy=None):
        calls.append((engine, query, limit, proxy))
        if engine == "bing":
            raise TimeoutError("Bing navigation timeout")
        return [{"title": "Prometheus", "link": "https://prometheus.io/", "snippet": "Monitoring"}]

    monkeypatch.setattr(starsearch_scrape, "search_web", fake_search)
    searcher = Searcher(
        browser=None,
        health_registry=SearchEngineHealthRegistry(),
    )
    results = await searcher.search("prometheus", limit=3, scrape_results=False)

    assert [call[0] for call in calls] == ["bing", "brave"]
    assert results[0].metadata["url"] == "https://prometheus.io/"
    assert results[0].metadata["engine"] == "brave"
    assert searcher.last_metadata.selected_engine == "brave"
    assert searcher.last_metadata.fallback_used is True
    assert [attempt.status for attempt in searcher.last_metadata.attempts] == ["error", "success"]


@pytest.mark.asyncio
async def test_explicit_engine_never_substitutes_destination(monkeypatch):
    calls = []

    async def fake_search(engine, query, limit, *, proxy=None):
        calls.append(engine)
        return []

    monkeypatch.setattr(starsearch_scrape, "search_web", fake_search)
    searcher = Searcher(browser=None, health_registry=SearchEngineHealthRegistry())
    results = await searcher.search("no hits", engine="brave", scrape_results=False)

    assert results == []
    assert calls == ["brave"]
    assert [attempt.engine for attempt in searcher.last_metadata.attempts] == ["brave"]


@pytest.mark.asyncio
async def test_configured_proxy_lease_reaches_serp_and_search_metadata(monkeypatch):
    endpoint = ProxyEndpoint.parse("http://account:secret@proxy.example:8080")
    provider = StaticProxyProvider([endpoint])
    lease = provider.acquire("search-proof")
    calls = []

    async def fake_search(engine, query, limit, *, proxy=None):
        calls.append(proxy)
        return [
            {
                "title": "Prometheus",
                "link": "https://prometheus.io/",
                "snippet": "Monitoring",
                "_egress": {
                    "gateway_enforced": True,
                    "mode": "upstream",
                    "upstream_scheme": "http",
                    "upstream_identity": endpoint.starsearch_identity,
                    "resolution": "local_frozen",
                },
            }
        ]

    monkeypatch.setattr(starsearch_scrape, "search_web", fake_search)
    searcher = Searcher(
        browser=None,
        health_registry=SearchEngineHealthRegistry(),
        proxy_provider=provider,
        proxy_lease=lease,
    )

    results = await searcher.search("prometheus", limit=1, scrape_results=False)

    assert calls == [endpoint.as_browser_proxy()]
    assert results[0].metadata["egress"]["gateway_enforced"] is True
    assert results[0].metadata["egress"]["upstream_identity"] == endpoint.starsearch_identity
    assert results[0].metadata["egress"]["provider"] == {
        "mode": "static",
        "proxied": True,
        "endpoint": endpoint.label,
    }
    assert provider.status()["endpoints"][0]["successes"] == 1


@pytest.mark.asyncio
async def test_search_proxy_failure_never_retries_direct(monkeypatch):
    endpoint = ProxyEndpoint.parse("socks5://account:secret@proxy.example:1080")
    provider = StaticProxyProvider([endpoint], failure_threshold=1, cooldown_seconds=60)
    lease = provider.acquire("search-proof")
    calls = []

    async def fail(engine, query, limit, *, proxy=None):
        calls.append((engine, proxy))
        raise ConnectionError("ERR_PROXY_CONNECTION_FAILED")

    monkeypatch.setattr(starsearch_scrape, "search_web", fail)
    searcher = Searcher(
        browser=None,
        health_registry=SearchEngineHealthRegistry(),
        proxy_provider=provider,
        proxy_lease=lease,
    )

    assert await searcher.search("prometheus", scrape_results=False) == []
    assert [name for name, _ in calls] == ["bing", "brave", "duckduckgo"]
    assert all(proxy == endpoint.as_browser_proxy() for _, proxy in calls)
    assert provider.status()["endpoints"][0]["healthy"] is False


@pytest.mark.asyncio
async def test_result_scrapes_reuse_the_same_proxy_lease(monkeypatch):
    endpoint = ProxyEndpoint.parse("https://account:secret@proxy.example:8443")
    provider = StaticProxyProvider([endpoint])
    lease = provider.acquire("search-proof")

    async def fake_search(engine, query, limit, *, proxy=None):
        return [
            {
                "title": "Example",
                "link": "https://example.com/",
                "snippet": "Example",
                "_egress": {
                    "gateway_enforced": True,
                    "mode": "upstream",
                    "upstream_scheme": "https",
                    "upstream_identity": endpoint.starsearch_identity,
                    "resolution": "local_frozen",
                },
            }
        ]

    scrape_calls = []

    async def fake_scrape(**kwargs):
        scrape_calls.append(kwargs)
        from huginn.models import ScrapeData

        return ScrapeData(
            markdown="# Example",
            metadata={
                "egress": {
                    "gateway_enforced": True,
                    "mode": "upstream",
                    "upstream_scheme": "https",
                    "upstream_identity": endpoint.starsearch_identity,
                    "resolution": "local_frozen",
                }
            },
        )

    monkeypatch.setattr(starsearch_scrape, "search_web", fake_search)
    searcher = Searcher(
        browser=None,
        health_registry=SearchEngineHealthRegistry(),
        proxy_provider=provider,
        proxy_lease=lease,
    )
    monkeypatch.setattr(searcher.scraper, "scrape", fake_scrape)

    results = await searcher.search("example", limit=1, scrape_results=True)

    assert scrape_calls[0]["proxy"] == endpoint.as_browser_proxy()
    assert results[0].metadata["egress"]["provider"]["endpoint"] == endpoint.label


@pytest.mark.asyncio
async def test_search_route_returns_selection_metadata(monkeypatch):
    from huginn.models import SearchMetadata, SearchOptions, SearchRequest, SearchResultItem
    from huginn.routers import search as search_router
    from huginn.state import get_state, reset_state

    captured = {}

    class FakeSearcher:
        def __init__(self, browser, fallback_chain=True, **kwargs):
            self.last_metadata = SearchMetadata(selected_engine="brave")

        async def search(self, **kwargs):
            captured.update(kwargs)
            return [SearchResultItem(metadata={"url": "https://example.com", "engine": "brave"})]

    reset_state()
    get_state().browser = object()
    monkeypatch.setattr(search_router, "Searcher", FakeSearcher)
    try:
        response = await search_router._do_search(
            SearchRequest(
                query="example",
                search_options=SearchOptions(engine="brave"),
                scrape_results=False,
            )
        )
    finally:
        reset_state()

    assert response.success is True
    assert response.metadata.selected_engine == "brave"
    assert captured["engine"] == "brave"


@pytest.mark.asyncio
async def test_search_route_returns_structured_engine_failure(monkeypatch):
    from huginn.models import SearchEngineAttempt, SearchEngineError, SearchMetadata, SearchRequest
    from huginn.routers import search as search_router
    from huginn.state import get_state, reset_state

    class FakeSearcher:
        def __init__(self, browser, fallback_chain=True, **kwargs):
            self.last_metadata = SearchMetadata(
                attempts=[
                    SearchEngineAttempt(
                        engine="bing",
                        status="error",
                        error=SearchEngineError(code="timeout", message="timed out"),
                    ),
                    SearchEngineAttempt(
                        engine="brave",
                        status="empty",
                        error=SearchEngineError(code="empty_results", message="empty"),
                    ),
                ]
            )

        async def search(self, **kwargs):
            return []

    reset_state()
    get_state().browser = object()
    monkeypatch.setattr(search_router, "Searcher", FakeSearcher)
    try:
        response = await search_router._do_search(SearchRequest(query="example"))
    finally:
        reset_state()

    assert response.success is False
    assert response.error_code == "search_engines_unavailable"
    assert [attempt.error.code for attempt in response.metadata.attempts] == [
        "timeout",
        "empty_results",
    ]


@pytest.mark.asyncio
async def test_search_route_fails_closed_when_proxy_inventory_is_exhausted():
    from huginn.models import SearchRequest
    from huginn.routers import search as search_router
    from huginn.state import get_state, reset_state

    endpoint = ProxyEndpoint.parse("http://account:secret@proxy.example:8080")
    provider = StaticProxyProvider([endpoint], failure_threshold=1, cooldown_seconds=60)
    provider.report_failure(endpoint, "proxy authentication failed")
    reset_state()
    get_state().browser = object()
    get_state().proxy_provider = provider
    try:
        response = await search_router._do_search(SearchRequest(query="example"))
    finally:
        reset_state()

    assert response.success is False
    assert response.error_code == "proxy_unavailable"
    assert "cooling down" in response.error
