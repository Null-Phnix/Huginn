"""Tests for Huginn CLI."""

from click.testing import CliRunner
from huginn.cli import cli


runner = CliRunner()


class TestCLIHelp:
    """Test CLI help output."""

    def test_main_help(self):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Huginn" in result.output
        assert "scrape" in result.output
        assert "crawl" in result.output


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


class TestCLIConfig:
    """Test config subcommand."""

    def test_config_shows_data(self):
        result = runner.invoke(cli, ["config"])
        assert result.exit_code == 0
        assert "data_dir" in result.output
        assert "db_path" in result.output
        assert "browser" in result.output
