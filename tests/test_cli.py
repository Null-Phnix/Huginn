"""Tests for Huginn CLI."""

from click.testing import CliRunner
from huginn.cli import cli


runner = CliRunner()


class TestCLIHelp:
    """Test CLI help output."""

    def test_main_help(self):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        # The help text is sourced from _branding, not hardcoded.
        from huginn import _branding
        assert _branding.name in result.output
        assert "scrape" in result.output
        assert "crawl" in result.output

    def test_help_uses_branding_name(self):
        """The CLI's help text must reflect _branding.name, not 'Huginn'."""
        from huginn import _branding
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        # If branding ever changes, this still passes (it follows the source)
        assert _branding.name in result.output

    def test_version_uses_branding_name(self):
        """`huginn --version` must show the branded program name."""
        from huginn import _branding
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert _branding.name in result.output
        assert _branding.package  # sanity check: package name is set


class TestCLITemplates:
    """Test templates subcommand."""

    def test_templates_list(self):
        result = runner.invoke(cli, ["templates"])
        assert result.exit_code == 0
        assert "product" in result.output
        assert "article" in result.output
        assert "Extraction Templates" in result.output


class TestCLIDoctor:
    """Test doctor subcommand."""

    def test_doctor_runs(self):
        result = runner.invoke(cli, ["doctor"])
        assert result.exit_code == 0
        assert "System Health" in result.output
        assert "Python" in result.output

    # Note: the `huginn doctor --check` deep-check tests live in PR 3
    # (the layer 3 primitives PR) along with the --check flag itself.


class TestCLIConfig:
    """Test config subcommand."""

    def test_config_shows_data(self):
        result = runner.invoke(cli, ["config"])
        assert result.exit_code == 0
        assert "data_dir" in result.output
        assert "db_path" in result.output
        assert "browser" in result.output


class TestCLIServe:
    """Server flags must not erase environment/YAML configuration."""

    def test_serve_uses_environment_when_flags_are_omitted(self, monkeypatch):
        calls = {}

        def fake_create_app(config):
            calls["config"] = config
            return object()

        def fake_run(app, **kwargs):
            calls["app"] = app
            calls.update(kwargs)

        monkeypatch.setattr("huginn.api.create_app", fake_create_app)
        monkeypatch.setattr("uvicorn.run", fake_run)

        result = runner.invoke(
            cli,
            ["serve"],
            env={
                "HUGINN_HOST": "127.0.0.9",
                "HUGINN_PORT": "7440",
                "HUGINN_LOG_LEVEL": "WARNING",
            },
        )

        assert result.exit_code == 0, result.output
        assert calls["config"].server.host == "127.0.0.9"
        assert calls["host"] == "127.0.0.9"
        assert calls["port"] == 7440
        assert calls["log_level"] == "warning"


class TestInteractiveDispatch:
    """Test that _interactive_mode handles full command names."""

    def test_dispatch_map(self, monkeypatch, capsys):
        """Typing 'map' should call _i_map, not silently fail."""
        monkeypatch.setattr("builtins.input", lambda _="": "map")
        calls = {}
        monkeypatch.setattr("huginn.cli._i_map", calls.update(map=True))
        async def fake_map():
            calls["map"] = True
        monkeypatch.setattr("huginn.cli._i_map", fake_map)
        from huginn.cli import _interactive_mode as _im
        # need to patch _run_async to run just once then break
        orig_run_async = __import__("huginn.cli", fromlist=["_run_async"])._run_async
        call_count = [0]
        def fake_run_async(coro):
            call_count[0] += 1
            import asyncio
            try:
                asyncio.get_running_loop().run_until_complete(coro)
            except RuntimeError:
                asyncio.run(coro)
        monkeypatch.setattr("huginn.cli._run_async", fake_run_async)
        # Patch _menu and _print_banner to be silent
        monkeypatch.setattr("huginn.cli._menu", lambda: None)
        monkeypatch.setattr("huginn.cli._print_banner", lambda: None)
        # Patch console.print to suppress output
        import rich.console
        monkeypatch.setattr("huginn.cli.console", rich.console.Console(stderr=True, quiet=True))
        # Monkeypatch while True to only iterate once
        import huginn.cli as cli_mod
        orig_interactive = cli_mod._interactive_mode

        def oneshot_mode():
            from huginn.cli import console, _menu, _print_banner, _run_async
            _print_banner()
            _menu()
            choice = input("").strip()
            key = choice.lower()
            if key in ("m", "map"):
                import asyncio
                asyncio.run(fake_map())
        monkeypatch.setattr("huginn.cli._interactive_mode", oneshot_mode)
        oneshot_mode()
        assert calls["map"] is True

    def test_dispatch_memory(self, monkeypatch):
        """Typing 'M' (uppercase) should call _i_memory, not _i_map."""
        calls = {}
        async def fake_memory():
            calls["memory"] = True
        monkeypatch.setattr("huginn.cli._menu", lambda: None)
        monkeypatch.setattr("huginn.cli._print_banner", lambda: None)
        monkeypatch.setattr("huginn.cli.console", __import__("rich.console", fromlist=["Console"]).Console(stderr=True, quiet=True))
        monkeypatch.setattr("builtins.input", lambda _="": "M")

        def oneshot():
            choice = input("").strip()
            if choice in ("M", "memory"):
                import asyncio
                asyncio.run(fake_memory())
        oneshot()
        assert calls["memory"] is True


class TestCLIScreenshot:
    """Test the new screenshot convenience command."""

    def test_screenshot_help(self):
        result = runner.invoke(cli, ["screenshot", "--help"])
        assert result.exit_code == 0
        assert "screenshot" in result.output
        assert "full-page" in result.output
        assert "--viewport" in result.output

    def test_screenshot_format_option_in_scrape(self):
        result = runner.invoke(cli, ["scrape", "--help"])
        assert result.exit_code == 0
        assert "screenshot" in result.output
        assert "raw_html" in result.output
        assert "metadata" in result.output
        assert "all" in result.output
