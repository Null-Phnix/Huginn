"""Retired compatibility entry point.

An early BlackCrawl snapshot carried a second copy of Blackreach's Flask job
gateway here. It imported a sibling repository through a machine-specific
path, competed with Huginn for port 7432, and bypassed the suite's current
authentication and persistence boundaries. Keeping that implementation live
made it far too easy to start the wrong service.

The production entry points are now:

* ``huginn serve`` (or this repository's Docker Compose service) on 7432;
* Blackreach's ``blackreach-http`` user service on 7434; and
* the Node ``blackreach-mcp`` adapter for every MCP client.

This small fail-fast module preserves a useful error for old launch commands
without preserving a competing runtime.
"""


def main() -> None:
    raise SystemExit(
        "This copied Blackreach HTTP gateway is retired. Start Huginn with "
        "`docker compose up -d` or `huginn serve`, and register only the "
        "blackreach-mcp adapter documented in Blackreach/docs/WEB_TOOL_SUITE.md."
    )


if __name__ == "__main__":
    main()
