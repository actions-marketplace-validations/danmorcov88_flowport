from typer.testing import CliRunner

from flowport import __version__
from flowport.cli import app

runner = CliRunner()


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"flowport {__version__}"
