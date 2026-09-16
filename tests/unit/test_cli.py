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


def test_migrate_variables_writes_three_files_and_leaves_input_alone(
    flow_gz: Path, tmp_path: Path
) -> None:
    before = hashlib.sha256(flow_gz.read_bytes()).hexdigest()
    out = tmp_path / "migrated" / "flow.json.gz"
    result = runner.invoke(app, ["migrate", "variables", str(flow_gz), "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert hashlib.sha256(flow_gz.read_bytes()).hexdigest() == before
    assert out.read_bytes()[:2] == b"\x1f\x8b"
    changes = json.loads((out.parent / "changes.json").read_text(encoding="utf-8"))
    assert {c["kind"] for c in changes} == {
        "create-context",
        "add-parameter",
        "assign-context",
        "rewrite-property",
        "remove-variable",
    }
    report = (out.parent / "report.md").read_text(encoding="utf-8")
    assert "## Migration" in report
    assert "NIFI2-VARIABLE-PARAMETER-COLLISION" in report
    assert "NIFI2-VARIABLE-REFERENCE-ATTRIBUTE-SCOPE" in report
    # running again gives the same bytes
    again = tmp_path / "again" / "flow.json.gz"
    assert (
        runner.invoke(app, ["migrate", "variables", str(flow_gz), "-o", str(again)]).exit_code == 0
    )
    assert again.read_bytes() == out.read_bytes()
    assert (again.parent / "changes.json").read_bytes() == (
        out.parent / "changes.json"
    ).read_bytes()


def test_migrate_variables_dry_run_writes_nothing(definitions_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out" / "variables.json"
    result = runner.invoke(
        app,
        [
            "migrate",
            "variables",
            str(definitions_dir / "variables.json"),
            "-o",
            str(out),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "nothing written" in result.output
    assert "rewrite-property" in result.output
    assert not out.parent.exists()


def test_migrate_variables_refuses_to_overwrite_the_input(flow_gz: Path) -> None:
    result = runner.invoke(app, ["migrate", "variables", str(flow_gz), "-o", str(flow_gz)])
    assert result.exit_code == 2


def test_migrate_variables_keep_unused(definitions_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "variables.json"
    args = ["migrate", "variables", str(definitions_dir / "variables.json"), "-o", str(out)]
    assert runner.invoke(app, [*args, "--keep-unused"]).exit_code == 0
    changes = json.loads((tmp_path / "changes.json").read_text(encoding="utf-8"))
    added = {c["property"] for c in changes if c["kind"] == "add-parameter"}
    assert "unused.var" in added
    assert out.read_bytes()[:1] == b"{"  # same kind as the input, no gzip without .gz


def test_migrate_templates_writes_one_definition_and_report_per_template(
    flow_gz: Path, tmp_path: Path
) -> None:
    before = hashlib.sha256(flow_gz.read_bytes()).hexdigest()
    out = tmp_path / "templates"
    result = runner.invoke(app, ["migrate", "templates", str(flow_gz), "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert hashlib.sha256(flow_gz.read_bytes()).hexdigest() == before
    assert sorted(p.name for p in out.iterdir()) == [
        "removed-components-template.json",
        "removed-components-template.report.md",
        "template-conversion-template.json",
        "template-conversion-template.report.md",
    ]
    definition = json.loads((out / "template-conversion-template.json").read_text("utf-8"))
    assert definition["flowContents"]["name"] == "Template Conversion Template"
    assert definition["flowEncodingVersion"] == "1.0"
    report = (out / "removed-components-template.report.md").read_text(encoding="utf-8")
    assert "## Migration" in report
    assert "NIFI2-REMOVED-COMPONENT" in report
    assert "processors converted | 4" in report
    # the converted definition is itself valid analyzer input
    assert (
        runner.invoke(app, ["analyze", str(out / "template-conversion-template.json")]).exit_code
        == 0
    )


def test_migrate_templates_rejects_a_definition_and_handles_no_templates(
    definitions_dir: Path, tmp_path: Path
) -> None:
    result = runner.invoke(
        app, ["migrate", "templates", str(definitions_dir / "clean.json"), "-o", str(tmp_path)]
    )
    assert result.exit_code == 2
    empty = tmp_path / "empty.json"
    empty.write_text('{"rootGroup": {"name": "root"}, "templates": []}', encoding="utf-8")
    result = runner.invoke(app, ["migrate", "templates", str(empty), "-o", str(tmp_path / "out")])
    assert result.exit_code == 0
    assert "No templates" in result.output
    assert not (tmp_path / "out").exists()


def test_migrate_template_converts_an_xml_export(fixtures_dir: Path, tmp_path: Path) -> None:
    xml = fixtures_dir / "nifi-1.28.1" / "templates" / "template-conversion.xml"
    out = tmp_path / "sub" / "converted.json"
    result = runner.invoke(app, ["migrate", "template", str(xml), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists() and (tmp_path / "sub" / "converted.report.md").exists()
    # same result as from the flow's copy of the template
    from_flow = tmp_path / "flow"
    runner.invoke(
        app,
        [
            "migrate",
            "templates",
            str(fixtures_dir / "nifi-1.28.1" / "flow.json.gz"),
            "-o",
            str(from_flow),
        ],
    )
    assert out.read_bytes() == (from_flow / "template-conversion-template.json").read_bytes()
    assert runner.invoke(app, ["migrate", "template", str(xml), "-o", str(xml)]).exit_code == 2
    assert (
        runner.invoke(
            app, ["migrate", "template", str(tmp_path / "nope.xml"), "-o", str(out)]
        ).exit_code
        == 2
    )


def test_migrate_components_writes_flow_changes_and_report(
    definitions_dir: Path, tmp_path: Path
) -> None:
    source = definitions_dir / "replacements.json"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    out = tmp_path / "migrated" / "replacements.json"
    result = runner.invoke(app, ["migrate", "components", str(source), "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    changes = json.loads((out.parent / "changes.json").read_text(encoding="utf-8"))
    assert {c["kind"] for c in changes} >= {
        "replace-component",
        "rename-property",
        "map-relationship",
    }
    report = (out.parent / "report.md").read_text(encoding="utf-8")
    assert "NIFI2-COMPONENT-REPLACED" in report and "NIFI2-REPLACEMENT-SKIPPED" in report
    assert "replace-component | 9" in report
    dry = runner.invoke(
        app, ["migrate", "components", str(source), "-o", str(tmp_path / "x.json"), "--dry-run"]
    )
    assert dry.exit_code == 0 and "nothing written" in dry.output
    assert not (tmp_path / "x.json").exists()
