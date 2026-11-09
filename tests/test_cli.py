from typer.testing import CliRunner

from postdesk import __version__
from postdesk.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == __version__


def test_unbuilt_commands_say_so():
    for cmd in ("doctor", "serve", "cron"):
        result = runner.invoke(app, [cmd])
        assert result.exit_code == 1
        assert "not built yet" in result.output
