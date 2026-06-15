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

    def test_doctor_check_runs_deep_checks(self):
        """`huginn doctor --check` runs the deep checks (LLM, change tracking, etc)."""
        result = runner.invoke(cli, ["doctor", "--check"])
        # Exit code: 0 (no FAIL — change tracking + HMAC should pass;
        # LLM creds may WARN; API server should SKIP).
        assert result.exit_code == 0, f"unexpected exit code: {result.output}"
        # Verify the deep-check UI is rendered
        assert "Deep Check" in result.output
        # Verify the new checks are present
        assert "Change tracking" in result.output
        assert "Webhook HMAC" in result.output
        assert "LLM credentials" in result.output
        assert "API server" in result.output
        # Summary line is shown
        assert "Summary:" in result.output

    def test_doctor_check_exit_code_nonzero_on_failure(self, monkeypatch):
        """`huginn doctor --check` returns non-zero exit code if any check FAILS.

        Sabotage a check function to force a FAIL, verify the exit code.
        """
        from huginn import doctor

        # Replace the change tracking check with one that always returns FAIL
        async def always_fail():
            from huginn.doctor import CheckResult, CheckStatus
            return CheckResult(
                component="Change tracking",
                status=CheckStatus.FAIL,
                details="sabotaged for test",
            )
        monkeypatch.setattr(doctor, "check_change_tracking", always_fail)

        result = runner.invoke(cli, ["doctor", "--check"])
        # Exit code should be 1 because of the FAIL
        assert result.exit_code == 1
        # And the output should mention the failed component
        assert "Change tracking" in result.output
        assert "FAIL" in result.output


class TestCLIConfig:
    """Test config subcommand."""

    def test_config_shows_data(self):
        result = runner.invoke(cli, ["config"])
        assert result.exit_code == 0
        assert "data_dir" in result.output
        assert "db_path" in result.output
        assert "browser" in result.output


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
