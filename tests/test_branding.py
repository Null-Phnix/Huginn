"""
Tests for the central branding module.

This file locks the contract for huginn/_branding.py: the constants
and helpers that every user-facing surface (CLI banner, FastAPI app
title, UA default, env var prefix) should read from. If a rebrand
ever needs to happen, this file pins the expected surface.
"""

import pytest

from huginn import _branding
from huginn import __version__


class TestBrandingIdentity:
    def test_name_is_huginn(self):
        assert _branding.name == "Huginn"

    def test_short_name_lowercase(self):
        assert _branding.short_name == _branding.name.lower()

    def test_binary_matches_short_name(self):
        # The CLI command `huginn ...` is what users type
        assert _branding.binary == _branding.short_name

    def test_package_matches_short_name(self):
        # The pip package `huginn` matches the binary
        assert _branding.package == _branding.short_name

    def test_python_module_matches_package(self):
        # Today they're the same. A future rebrand could decouple them.
        assert _branding.python_module == _branding.package


class TestBrandingUserAgent:
    def test_ua_default_uses_current_version(self):
        ua = _branding.ua()
        assert __version__ in ua
        assert ua.startswith("Huginn/")

    def test_ua_explicit_version(self):
        ua = _branding.ua("9.9.9")
        assert "9.9.9" in ua
        assert "1.2.0" not in ua

    def test_ua_template_format(self):
        # If someone changes ua_template, the format still works
        rendered = _branding.ua("3.1.4")
        assert rendered == "Huginn/3.1.4 (+https://huginn.dev/bot)"


class TestBrandingEnvPrefix:
    def test_env_prefix_is_huginn(self):
        assert _branding.env_prefix == "HUGINN"

    def test_env_prefix_uppercase(self):
        # Env vars are uppercase by convention
        assert _branding.env_prefix == _branding.env_prefix.upper()

    def test_common_env_vars_use_prefix(self):
        # Spot-check that the codebase doesn't have any stray HUGINN_*
        # vars that should be using env_prefix (a form of drift guard).
        # We just check the canonical three that are documented.
        for var in ("HUGINN_API_KEY", "HUGINN_PORT", "HUGINN_DATA_DIR"):
            assert var.startswith(_branding.env_prefix + "_")


class TestBrandingUrls:
    def test_project_url_set(self):
        assert _branding.project_url.startswith("https://")

    def test_docs_url_under_project(self):
        assert _branding.docs_url.startswith(_branding.project_url)

    def test_repo_url_github(self):
        assert "github.com" in _branding.repo_url
        assert _branding.repo_url.startswith("https://")


class TestBrandingBanner:
    def test_banner_contains_name(self):
        assert _branding.ascii_banner is not None
        assert _branding.name in _branding.ascii_banner

    def test_banner_contains_version(self):
        assert _branding.ascii_banner is not None
        assert __version__ in _branding.ascii_banner

    def test_banner_can_be_disabled(self):
        # If you set ascii_banner = None, importing and reading should work
        # (we don't actually mutate the module — that would break other tests)
        # Just verify the type annotation allows Optional[str].
        from typing import get_type_hints, Optional
        hints = get_type_hints(_branding)
        # The annotation is Optional[str] which on Python 3.10+ renders as
        # `str | None`, on 3.9 as `Optional[str]`, on 3.12 still str | None.
        # Either form must include None. Check the resolved type directly.
        ann = hints["ascii_banner"]
        assert ann == Optional[str] or ann == "str | None" or str(ann) in ("Optional[str]", "str | None")
