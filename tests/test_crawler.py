"""
Tests for Huginn Crawler — URL normalization, path filtering, BFS logic.
"""

import pytest

from huginn.crawler import Crawler, CrawlResult


class TestURLNormalization:
    """Test URL normalization for deduplication."""

    def setup_method(self):
        self.crawler = Crawler(browser=None)  # No browser needed for unit tests

    def test_strip_fragment(self):
        assert self.crawler._normalize_url("https://example.com/page#top") == "https://example.com/page"

    def test_strip_trailing_slash(self):
        assert self.crawler._normalize_url("https://example.com/path/") == "https://example.com/path"

    def test_root_path_stays(self):
        assert self.crawler._normalize_url("https://example.com/") == "https://example.com/"

    def test_sort_query_params(self):
        result = self.crawler._normalize_url("https://example.com/search?q=test&page=1")
        expected = self.crawler._normalize_url("https://example.com/search?page=1&q=test")
        assert result == expected

    def test_same_url_normalized_same(self):
        urls = [
            "https://example.com/page",
            "https://example.com/page/",
            "https://example.com/page#section",
        ]
        normalized = [self.crawler._normalize_url(u) for u in urls]
        assert len(set(normalized)) == 1

    def test_different_urls_stay_different(self):
        urls = ["https://example.com/about", "https://example.com/contact"]
        normalized = [self.crawler._normalize_url(u) for u in urls]
        assert len(set(normalized)) == 2


class TestPathFiltering:
    """Test include/exclude path filtering."""

    def setup_method(self):
        self.crawler = Crawler(browser=None)

    def test_include_paths_exact(self):
        self.crawler.include_paths = ["/docs/"]
        assert self.crawler._should_follow("https://example.com/docs/guide", "example.com", 1) is True
        assert self.crawler._should_follow("https://example.com/blog/post", "example.com", 1) is False

    def test_include_paths_glob(self):
        self.crawler.include_paths = ["/docs/*"]
        assert self.crawler._should_follow("https://example.com/docs/api", "example.com", 1) is True
        assert self.crawler._should_follow("https://example.com/docs/", "example.com", 1) is True

    def test_exclude_paths_exact(self):
        self.crawler.exclude_paths = ["/admin/"]
        assert self.crawler._should_follow("https://example.com/admin/settings", "example.com", 1) is False
        assert self.crawler._should_follow("https://example.com/public/page", "example.com", 1) is True

    def test_exclude_paths_glob(self):
        self.crawler.exclude_paths = ["/private/*"]
        assert self.crawler._should_follow("https://example.com/private/data", "example.com", 1) is False
        assert self.crawler._should_follow("https://example.com/public/data", "example.com", 1) is True

    def test_include_and_exclude_combined(self):
        self.crawler.include_paths = ["/docs/"]
        self.crawler.exclude_paths = ["/docs/internal/"]
        assert self.crawler._should_follow("https://example.com/docs/api", "example.com", 1) is True
        assert self.crawler._should_follow("https://example.com/docs/internal/secret", "example.com", 1) is False

    def test_skip_non_content_extensions(self):
        assert self.crawler._should_follow("https://example.com/file.pdf", "example.com", 1) is False
        assert self.crawler._should_follow("https://example.com/file.zip", "example.com", 1) is False
        assert self.crawler._should_follow("https://example.com/image.jpg", "example.com", 1) is False
        assert self.crawler._should_follow("https://example.com/page", "example.com", 1) is True

    def test_external_domain_blocked(self):
        assert self.crawler._should_follow("https://other.com/page", "example.com", 1) is False

    def test_external_domain_allowed(self):
        self.crawler.allow_external = True
        assert self.crawler._should_follow("https://other.com/page", "example.com", 1) is True

    def test_depth_limit(self):
        self.crawler.max_depth = 2
        assert self.crawler._should_follow("https://example.com/page", "example.com", 2) is True
        assert self.crawler._should_follow("https://example.com/deep", "example.com", 3) is False

    def test_http_s_only(self):
        assert self.crawler._should_follow("https://example.com/page", "example.com", 1) is True
        assert self.crawler._should_follow("javascript:void(0)", "example.com", 1) is False
        assert self.crawler._should_follow("mailto:test@example.com", "example.com", 1) is False


class TestCrawlResult:
    """Test CrawlResult container."""

    def test_init(self):
        result = CrawlResult(job_id="test-123")
        assert result.job_id == "test-123"
        assert len(result.pages) == 0
        assert len(result.visited) == 0
        assert len(result.errors) == 0
        assert result.completed == 0

    def test_elapsed_time(self):
        import time
        result = CrawlResult(job_id="test")
        # elapsed should be positive
        time.sleep(0.01)
        assert result.elapsed > 0


class TestCrawlerCancellation:
    """Test crawl cancellation."""

    def test_cancel_signal(self):
        crawler = Crawler(browser=None)
        assert crawler._cancel is False
        crawler.cancel()
        assert crawler._cancel is True


class TestContentHashing:
    """Test content hashing for duplicate detection."""

    def test_content_hash_deterministic(self):
        from huginn.crawler import content_hash
        h1 = content_hash("Hello world")
        h2 = content_hash("Hello world")
        assert h1 == h2

    def test_content_hash_different_content(self):
        from huginn.crawler import content_hash
        h1 = content_hash("Hello world")
        h2 = content_hash("Goodbye world")
        assert h1 != h2

    def test_content_hash_normalizes_whitespace(self):
        from huginn.crawler import content_hash
        h1 = content_hash("Hello   world\n\nfoo")
        h2 = content_hash("Hello world\nfoo")
        assert h1 == h2

    def test_content_hash_case_insensitive(self):
        from huginn.crawler import content_hash
        h1 = content_hash("Hello World")
        h2 = content_hash("hello world")
        assert h1 == h2

    def test_content_hash_length(self):
        from huginn.crawler import content_hash
        h = content_hash("test")
        assert len(h) == 16  # truncated SHA-256

    def test_is_duplicate_new_content(self):
        crawler = Crawler(browser=None)
        seen = set()
        assert crawler.is_duplicate("new content", seen) is False
        assert "test" not in seen  # but the hash should be in seen now
        assert len(seen) == 1

    def test_is_duplicate_seen_content(self):
        crawler = Crawler(browser=None)
        seen = set()
        assert crawler.is_duplicate("duplicate content", seen) is False
        assert crawler.is_duplicate("duplicate content", seen) is True

    def test_is_duplicate_whitespace_insensitive(self):
        crawler = Crawler(browser=None)
        seen = set()
        assert crawler.is_duplicate("Hello   world", seen) is False
        assert crawler.is_duplicate("Hello world", seen) is True


class TestRobotsTxt:
    """Test robots.txt parsing and path checking."""

    def test_parse_disallow_all(self):
        from huginn.crawler import RobotsChecker
        checker = RobotsChecker("https://example.com")
        checker.rules = checker._parse("User-agent: *\nDisallow: /")
        assert checker.is_allowed("/anything") is False

    def test_parse_disallow_specific_paths(self):
        from huginn.crawler import RobotsChecker
        checker = RobotsChecker("https://example.com")
        checker.rules = checker._parse(
            "User-agent: *\nDisallow: /admin/\nDisallow: /private/\nAllow: /public/"
        )
        assert checker.is_allowed("/admin/settings") is False
        assert checker.is_allowed("/private/data") is False
        assert checker.is_allowed("/public/page") is True
        assert checker.is_allowed("/about") is True

    def test_parse_allow_overrides_disallow(self):
        from huginn.crawler import RobotsChecker
        checker = RobotsChecker("https://example.com")
        checker.rules = checker._parse(
            "User-agent: *\nDisallow: /admin/\nAllow: /admin/public/"
        )
        # More specific allow should override disallow
        assert checker.is_allowed("/admin/public/stats") is True
        assert checker.is_allowed("/admin/secret") is False

    def test_parse_no_rules_allows_all(self):
        from huginn.crawler import RobotsChecker
        checker = RobotsChecker("https://example.com")
        checker.rules = checker._parse("# No rules\n")
        assert checker.is_allowed("/anything") is True

    def test_parse_ignores_comments(self):
        from huginn.crawler import RobotsChecker
        checker = RobotsChecker("https://example.com")
        checker.rules = checker._parse("# This is a comment\nUser-agent: *\n# Another comment\nDisallow: /private/")
        assert checker.is_allowed("/private/thing") is False
        assert checker.is_allowed("/public/thing") is True

    def test_rules_not_fetched_allows_all(self):
        from huginn.crawler import RobotsChecker
        checker = RobotsChecker("https://example.com")
        # rules is None by default
        assert checker.rules is None
        assert checker.is_allowed("/anything") is True

    def test_fetch_error_allows_all(self):
        from huginn.crawler import RobotsChecker
        checker = RobotsChecker("https://example.com")
        # Empty rules = allow all (simulates fetch error)
        checker.rules = {"disallow": [], "allow": []}
        assert checker.is_allowed("/anything") is True

    def test_robots_url_construction(self):
        from huginn.crawler import RobotsChecker
        checker = RobotsChecker("https://example.com/some/path")
        assert checker.robots_url == "https://example.com/robots.txt"

    def test_robots_url_trailing_slash(self):
        from huginn.crawler import RobotsChecker
        checker = RobotsChecker("https://example.com/")
        assert checker.robots_url == "https://example.com/robots.txt"

    def test_parse_multiple_agents(self):
        from huginn.crawler import RobotsChecker
        checker = RobotsChecker("https://example.com")
        checker.rules = checker._parse(
            "User-agent: GoogleBot\nDisallow: /google-only/\nUser-agent: *\nDisallow: /all/"
        )
        assert checker.is_allowed("/all/thing") is False
        assert checker.is_allowed("/google-only/thing") is True  # Only * applies
        assert checker.is_allowed("/anything") is True


class TestCrawlRequestIgnoreRobots:
    """Test that CrawlRequest has ignore_robots field."""

    def test_default_ignore_robots(self):
        from huginn.models import CrawlRequest
        req = CrawlRequest(url="https://example.com")
        assert hasattr(req, "ignore_robots")
        assert req.ignore_robots is False

    def test_set_ignore_robots(self):
        from huginn.models import CrawlRequest
        req = CrawlRequest(url="https://example.com", ignore_robots=True)
        assert req.ignore_robots is True