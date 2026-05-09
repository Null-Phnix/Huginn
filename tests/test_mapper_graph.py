"""Unit tests for huginn/mapper.py graph mapping (Task 7)."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from huginn.models import PageNode, PageEdge, CrawlGraph
from huginn.mapper import Mapper


class FakeBrowser:
    """Minimal fake for Mapper browser dependency."""
    def __init__(self):
        self.last_status_code = 200

    async def new_context(self):
        return self

    async def new_page(self):
        return MagicMock()

    async def navigate(self, page, url):
        return True

    async def close(self):
        pass


@pytest.fixture
def mapper():
    return Mapper(browser=FakeBrowser())


class TestCrawlGraphModel:
    def test_empty_graph(self):
        g = CrawlGraph(start_url="https://example.com")
        assert g.total_discovered == 0
        assert g.total_crawled == 0
        assert g.nodes == []
        assert g.edges == []

    def test_graph_with_nodes_and_edges(self):
        g = CrawlGraph(
            start_url="https://example.com",
            nodes=[
                PageNode(url="https://example.com", title="Home", depth=0),
                PageNode(url="https://example.com/about", title="About", depth=1),
            ],
            edges=[
                PageEdge(source="https://example.com", target="https://example.com/about"),
            ],
            total_discovered=2,
            total_crawled=2,
        )
        assert g.total_discovered == 2
        assert len(g.edges) == 1
        assert g.nodes[0].depth == 0

    def test_graph_serialization(self):
        g = CrawlGraph(
            start_url="https://example.com",
            nodes=[PageNode(url="https://example.com", title="Home")],
            edges=[],
        )
        d = g.model_dump(mode="json")
        assert d["start_url"] == "https://example.com"
        assert len(d["nodes"]) == 1
        assert d["nodes"][0]["url"] == "https://example.com"


class TestMapSiteGraph:
    @pytest.mark.asyncio
    async def test_single_page_no_links(self, mapper):
        """A page with zero links returns just the start node."""
        mapper._extract_page_links = AsyncMock(return_value=(set(), "Home", 200))
        graph = await mapper.map_site_graph("https://example.com", max_depth=3)
        assert graph.start_url == "https://example.com"
        assert len(graph.nodes) == 1
        assert graph.nodes[0].url == "https://example.com"
        assert graph.nodes[0].title == "Home"
        assert graph.total_crawled == 1

    @pytest.mark.asyncio
    async def test_one_level_of_links(self, mapper):
        """Start page links to one internal page; external links filtered."""
        async def fake(url, *args, **kwargs):
            if url == "https://example.com":
                return ({"https://example.com/page1", "https://external.com/out"}, "Home", 200)
            return (set(), "Page", 200)

        mapper._extract_page_links = fake
        graph = await mapper.map_site_graph(
            "https://example.com", limit=10, max_depth=1
        )
        # external.com is fully filtered when include_subdomains=False
        assert len(graph.nodes) == 2  # start + page1
        assert graph.nodes[1].url == "https://example.com/page1"
        assert graph.nodes[1].depth == 1
        # edges: start->page1 only (page1 has no outgoing links in mock)
        assert len(graph.edges) == 1

    @pytest.mark.asyncio
    async def test_respects_max_depth(self, mapper):
        """Depth limit prevents crawling beyond max_depth."""
        async def fake(url, *args, **kwargs):
            if url == "https://example.com":
                return ({"https://example.com/p1"}, "Home", 200)
            if url == "https://example.com/p1":
                return ({"https://example.com/p2"}, "P1", 200)
            return (set(), "Deep", 200)

        mapper._extract_page_links = fake
        graph = await mapper.map_site_graph("https://example.com", max_depth=1, limit=10)
        # p2 is discovered as an edge but not re-crawled
        assert len(graph.nodes) == 2  # start + p1
        assert len(graph.edges) == 2  # start->p1, p1->p2
        # p2 edge exists but no node with title
        p2_edge = next((e for e in graph.edges if e.target.endswith("/p2")), None)
        assert p2_edge is not None

    @pytest.mark.asyncio
    async def test_respects_limit(self, mapper):
        """Limit stops BFS early."""
        counter = [0]
        async def fake(url, *args, **kwargs):
            idx = counter[0]
            counter[0] += 1
            links = {f"https://example.com/{idx}-{i}" for i in range(2)}
            return (links, f"Page {idx}", 200)

        mapper._extract_page_links = fake
        graph = await mapper.map_site_graph("https://example.com", limit=2, max_depth=3)
        # Should stop at 2 pages total
        assert len(graph.nodes) <= 2

    @pytest.mark.asyncio
    async def test_subdomains_included(self, mapper):
        """Subdomains are queued when explicitly included."""
        async def fake(url, *args, **kwargs):
            if url == "https://example.com":
                return ({"https://sub.example.com/page"}, "Home", 200)
            if "sub.example.com" in url:
                return (set(), "Sub Page", 200)
            return (set(), "Other", 200)

        mapper._extract_page_links = fake
        graph = await mapper.map_site_graph(
            "https://example.com", include_subdomains=True, max_depth=2
        )
        sub_node = next((n for n in graph.nodes if "sub.example.com" in n.url), None)
        assert sub_node is not None
        assert sub_node.title == "Sub Page"

    @pytest.mark.asyncio
    async def test_skips_non_http_schemes(self, mapper):
        """mailto:, javascript: etc. are skipped."""
        async def fake(url, *args, **kwargs):
            return ({"mailto:test@example.com", "javascript:void(0)", "https://example.com/page"}, "Home", 200)

        mapper._extract_page_links = fake
        graph = await mapper.map_site_graph("https://example.com")
        targets = [e.target for e in graph.edges]
        assert not any("mailto" in t for t in targets)
        assert not any("javascript" in t for t in targets)
        assert any("/page" in t for t in targets)

    @pytest.mark.asyncio
    async def test_duplicate_links_on_same_page(self, mapper):
        """Duplicate links from the same page get one edge (Set dedup)."""
        async def fake(url, *args, **kwargs):
            if url == "https://example.com":
                # The set literal deduplicates to size 2
                return ({"https://example.com/a", "https://example.com/a", "https://example.com/b"}, "Home", 200)
            return (set(), "Other", 200)

        mapper._extract_page_links = fake
        graph = await mapper.map_site_graph("https://example.com", limit=10)
        assert len(graph.nodes) == 3  # home + a + b
        # Only two unique edges from home: home->a, home->b
        home_edges = [e for e in graph.edges if e.source == "https://example.com"]
        assert len(home_edges) == 2
