"""
Huginn — branding.

Single source of truth for every user-facing string. If you want to
rebrand (different name, different color, different UA template), edit
this file and re-run the tests.

Why this exists: the codebase had "Huginn" hardcoded in cli.py, api.py,
__init__.py, the banner, the UA, the package name, the binary name, the
Python module name, and a dozen env var prefixes (HUGINN_*). When the
project was BlackCrawl, every one of those had to be hand-updated. This
module is the cheaper way.

What lives here:
  - name / short_name / binary / package — the obvious surface
  - ua_template — the user-agent string (uses __version__ at call time)
  - color — for ASCII art / rich console (purple to match the existing
    purple Huginn brand in README badges / docs)
  - ascii_banner — the post-launch banner (set to None to disable)
  - env_prefix — env var prefix (HUGINN_*, used by config._apply_env)
  - docs_url / support_url — outbound links

What does NOT live here:
  - the package version (__version__) — that stays in __init__.py so
    `import huginn; huginn.__version__` continues to work as a public API
  - pydantic field names, API paths, CLI command names — those are part
    of the wire/CLI surface, branding doesn't change them
"""

from __future__ import annotations

from typing import Optional

from . import __version__


# ─── identity ───────────────────────────────────────────────────────────────

name: str = "Huginn"
"""Display name. Used in CLI banner, FastAPI app title, README, etc."""

short_name: str = "huginn"
"""Lowercase identifier. Used in URLs, env var derivations, etc."""

binary: str = "huginn"
"""The `huginn` CLI command name (matches the [project.scripts] entry)."""

package: str = "huginn"
"""The pip distribution name. Matches pyproject.toml [project.name]."""

python_module: str = "huginn"
"""The Python import name. (Same as `package` today; kept separate so a
rebrand can decouple them later — e.g. package 'huginn-enterprise',
module 'huginn'.)"""


# ─── user agent ────────────────────────────────────────────────────────────

#: User-Agent template. The default is f-evaluated against `version` once
#: at access time so a version bump takes effect without a config reload.
ua_template: str = "Huginn/{version} (+https://huginn.dev/bot)"


def ua(version: Optional[str] = None) -> str:
    """Build the default User-Agent string for a given version (defaults
    to the current package version)."""
    return ua_template.format(version=version or __version__)


# ─── user-facing text ──────────────────────────────────────────────────────

#: Color used for the ASCII banner and CLI accents. Any rich-style color
#: name (purple, cyan, red, etc.).
color: str = "purple"

#: Tagline shown under the banner.
tagline: str = "Odin's raven — flies out, brings back knowledge."

#: Short project description (1 sentence, no period).
description: str = "Autonomous web scraping, crawling, and extraction API"

#: The post-launch ASCII banner. Set to None to disable the banner.
#: Built last so it can reference the constants above + ua() helper.
ascii_banner: Optional[str] = f"""
{color}  ╔══════════════════════════════════════════════════════════╗
  ║                                                          ║
  ║    {name} — Odin's Raven                                   ║
  ║    {tagline:<52s} ║
  ║                                                          ║
  ║    1M context · xhigh thinking · {ua().split(' ')[0]:<24s} ║
  ║    {package} {__version__:<47s} ║
  ║                                                          ║
  ╚══════════════════════════════════════════════════════════╝
""".strip()


# ─── env vars + URLs ───────────────────────────────────────────────────────

#: Prefix for every Huginn env var (HUGINN_API_KEY, HUGINN_PORT, etc.).
env_prefix: str = "HUGINN"

#: Project URL used in the UA and docs links.
project_url: str = "https://huginn.dev"

#: Docs URL (used in --help, error messages).
docs_url: str = f"{project_url}/docs"

#: Repo URL.
repo_url: str = "https://github.com/Null-Phnix/Huginn"
