"""
Tests for Huginn Mapper — Sitemap parsing, URL discovery.
"""

from xml.etree.ElementTree import Element, SubElement, tostring

import pytest

from huginn.mapper import Mapper


class TestSitemapParsing:
    """Test sitemap.xml parsing."""

    def setup_method(self):
        self.mapper = Mapper(browser=None)  # No browser for unit tests

    def test_parse_standard_sitemap(self):
        """Should parse a standard sitemap.xml."""
        urlset = Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
        for path in ["/", "/about", "/contact", "/blog"]:
            url = SubElement(urlset, "url")
            loc = SubElement(url, "loc")
            loc.text = f"https://example.com{path}"

        xml_text = tostring(urlset, encoding="unicode")
        urls = self.mapper._parse_sitemap_xml(xml_text, "https://example.com")
        assert len(urls) == 4
        assert "https://example.com/" in urls
        assert "https://example.com/about" in urls

    def test_parse_sitemap_index(self):
        """Should parse a sitemap index."""
        sitemapindex = Element("sitemapindex", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
        for path in ["/sitemap-posts.xml", "/sitemap-pages.xml"]:
            sitemap = SubElement(sitemapindex, "sitemap")
            loc = SubElement(sitemap, "loc")
            loc.text = f"https://example.com{path}"

        xml_text = tostring(sitemapindex, encoding="unicode")
        urls = self.mapper._parse_sitemap_xml(xml_text, "https://example.com")
        assert len(urls) == 2

    def test_parse_sitemap_with_namespace(self):
        """Should handle sitemaps with XML namespace."""
        ns = "http://www.sitemaps.org/schemas/sitemap/0.9"
        urlset = Element(f"{{{ns}}}urlset")
        url = SubElement(urlset, f"{{{ns}}}url")
        loc = SubElement(url, f"{{{ns}}}loc")
        loc.text = "https://example.com/page"

        xml_text = tostring(urlset, encoding="unicode")
        urls = self.mapper._parse_sitemap_xml(xml_text, "https://example.com")
        assert len(urls) == 1

    def test_parse_malformed_xml_fallback(self):
        """Should fall back to regex for malformed XML."""
        malformed = """
        <urlset>
            <url><loc>https://example.com/page1</loc></url>
            <url><loc>https://example.com/page2</loc></url>
            <broken>
        """
        urls = self.mapper._parse_sitemap_xml(malformed, "https://example.com")
        assert len(urls) == 2

    def test_parse_empty_sitemap(self):
        """Should return empty set for empty sitemap."""
        urls = self.mapper._parse_sitemap_xml("<urlset></urlset>", "https://example.com")
        assert len(urls) == 0

    def test_parse_sitemap_dedup(self):
        """Should deduplicate URLs."""
        urlset = Element("urlset")
        for _ in range(3):
            url = SubElement(urlset, "url")
            loc = SubElement(url, "loc")
            loc.text = "https://example.com/duplicate"

        xml_text = tostring(urlset, encoding="unicode")
        urls = self.mapper._parse_sitemap_xml(xml_text, "https://example.com")
        assert len(urls) == 1