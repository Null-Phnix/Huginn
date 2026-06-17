"""
Tests for ignoreInvalidURLs on /v1/batch/scrape — Firecrawl parity.

Feature: POST /v1/batch/scrape accepts ignoreInvalidURLs (camelCase) or
ignore_invalid_urls (snake_case). When True, invalid URLs (bad scheme,
missing host, empty string) are skipped with a warning. When False
(default), an invalid URL causes a 422.
"""

import pytest
from pydantic import TypeAdapter

from huginn.models import FlockRequest
from huginn.scraper import _is_valid_http_url


class TestFlockRequestIgnoreInvalidURLs:
    """FlockRequest.ignore_invalid_urls accepts and defaults correctly."""

    def test_flock_request_has_ignore_invalid_field(self):
        """FlockRequest.ignore_invalid_urls exists, defaults to False."""
        req = FlockRequest(urls=["https://example.com"])
        assert hasattr(req, "ignore_invalid_urls")
        assert req.ignore_invalid_urls is False

    def test_flock_request_accepts_ignore_invalid_snake(self):
        """ignore_invalid_urls (snake_case) is accepted via populate_by_name."""
        req = FlockRequest(urls=["https://example.com"], ignore_invalid_urls=True)
        assert req.ignore_invalid_urls is True

    def test_flock_request_accepts_ignore_invalid_camel(self):
        """ignoreInvalidURLs (camelCase) is accepted via alias."""
        adapter = TypeAdapter(FlockRequest)
        req = adapter.validate_python({"urls": ["https://example.com"], "ignoreInvalidURLs": True})
        assert req.ignore_invalid_urls is True


class TestIsValidHttpUrl:
    """_is_valid_http_url() — syntactic URL validation."""

    @pytest.mark.parametrize("url", [
        "https://example.com",
        "http://example.com",
        "https://example.com/path",
        "https://sub.example.com:8080/path?q=1#hash",
        "  https://example.com  ",  # whitespace ok
    ])
    def test_valid_urls_pass(self, url):
        assert _is_valid_http_url(url) is True, f"Expected valid: {url}"

    @pytest.mark.parametrize("url", [
        "",
        "   ",
        "not a url",
        "ftp://example.com",       # bad scheme
        "javascript:alert(1)",     # bad scheme
        "file:///etc/passwd",      # bad scheme
        "https://",                # missing host
        "example.com",             # missing scheme
    ])
    def test_invalid_urls_fail(self, url):
        assert _is_valid_http_url(url) is False, f"Expected invalid: {url}"


class TestFlockRequestMixedUrls:
    """FlockRequest accepts a mix of valid and invalid URLs (validation deferred to endpoint)."""

    def test_flock_accepts_mixed_urls(self):
        """Model accepts both valid and invalid URLs (endpoint decides what to do)."""
        req = FlockRequest(urls=["https://valid.com", "not a url", "ftp://bad.com"])
        assert len(req.urls) == 3

    def test_flock_requires_at_least_one_url(self):
        """urls must have at least 1 entry."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            FlockRequest(urls=[])
