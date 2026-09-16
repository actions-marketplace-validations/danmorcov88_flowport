import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from flowport import __version__
from flowport.cli import app

runner = CliRunner()


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"flowport {__version__}"


def test_analyze_terminal_output_and_exit_code(flow_gz: Path) -> None:
    result = runner.invoke(app, ["analyze", str(flow_gz)])
    assert result.exit_code == 1  # blockers present
    assert "BLOCKER" in result.output
    assert "NIFI2-EVENT-DRIVEN" in result.output


def test_analyze_clean_definition_exits_zero(definitions_dir: Path) -> None:
    result = runner.invoke(app, ["analyze", str(definitions_dir / "clean.json")])
    assert result.exit_code == 0
    assert "0 findings" in result.output


def test_fail_on_threshold(definitions_dir: Path) -> None:
    variables = str(definitions_dir / "variables.json")
    assert runner.invoke(app, ["analyze", variables, "--fail-on", "blocker"]).exit_code == 0
    assert runner.invoke(app, ["analyze", variables, "--fail-on", "manual"]).exit_code == 1
    assert runner.invoke(app, ["analyze", variables, "--fail-on", "info"]).exit_code == 1
    assert runner.invoke(app, ["analyze", variables, "--fail-on", "never"]).exit_code == 0


def test_input_errors_exit_two(tmp_path: Path, fixtures_dir: Path) -> None:
    missing = runner.invoke(app, ["analyze", str(tmp_path / "missing.json")])
    assert missing.exit_code == 2
    template = fixtures_dir / "nifi-1.28.1" / "templates" / "removed-components.xml"
    assert runner.invoke(app, ["analyze", str(template)]).exit_code == 2
    unknown_catalog = runner.invoke(app, ["analyze", str(template), "--target-version", "9.9.9"])
    assert unknown_catalog.exit_code == 2


def test_json_output_is_deterministic_and_input_untouched(flow_gz: Path, tmp_path: Path) -> None:
    before = hashlib.sha256(flow_gz.read_bytes()).hexdigest()
    mtime = flow_gz.stat().st_mtime_ns
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    for out in (first, second):
        result = runner.invoke(
            app,
            [
                "analyze",
                str(flow_gz),
                "--format",
                "json",
                "--output",
                str(out),
                "--fail-on",
                "never",
            ],
        )
        assert result.exit_code == 0, result.output
    assert first.read_bytes() == second.read_bytes()
    assert hashlib.sha256(flow_gz.read_bytes()).hexdigest() == before
    assert flow_gz.stat().st_mtime_ns == mtime
    report = json.loads(first.read_text(encoding="utf-8"))
    assert report["input"]["sha256"] == before
    assert report["summary"]["total"] == len(report["findings"])


def test_json_to_stdout(definitions_dir: Path) -> None:
    result = runner.invoke(
        app,
        ["analyze", str(definitions_dir / "scheduling.json"), "-f", "json", "--fail-on", "never"],
    )
    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report["summary"]["by_severity"]["BLOCKER"] == 2


def test_markdown_and_html_outputs(definitions_dir: Path, tmp_path: Path) -> None:
    source = str(definitions_dir / "scripting.json")
    md = tmp_path / "r.md"
    html = tmp_path / "r.html"
    assert (
        runner.invoke(
            app, ["analyze", source, "-f", "markdown", "-o", str(md), "--fail-on", "never"]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["analyze", source, "-f", "html", "-o", str(html), "--fail-on", "never"]
        ).exit_code
        == 0
    )
    text = md.read_text(encoding="utf-8")
    assert text.startswith("# flowport report")
    assert "NIFI2-SCRIPT-ENGINE" in text
    page = html.read_text(encoding="utf-8")
    assert page.startswith("<!DOCTYPE html>")
    assert "NIFI2-SCRIPT-ENGINE" in page
    assert "<link" not in page and 'src="http' not in page  # single file, no external assets
