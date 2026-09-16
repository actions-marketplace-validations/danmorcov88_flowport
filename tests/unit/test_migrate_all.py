"""migrate all: variables, then components, then templates, on one flow."""

import copy
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from flowport.catalog import Catalog, load_catalog
from flowport.cli import app
from flowport.loaders import load
from flowport.transforms import render_changes
from flowport.transforms.components import migrate_components
from flowport.transforms.pipeline import migrate_all
from flowport.transforms.variables import migrate_variables
from flowport.writers import dumps

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nifi-1.28.1"
GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "nifi-1.28.1" / "migrate-all"
runner = CliRunner()


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog()


def test_pipeline_equals_the_steps_in_order(catalog: Catalog) -> None:
    flow = load(FIXTURES / "flow.json.gz")
    before = copy.deepcopy(flow.raw)
    pipeline = migrate_all(flow, catalog)
    assert flow.raw == before
    stepwise = migrate_components(migrate_variables(flow, catalog).flow, catalog)
    assert dumps(pipeline.flow_result.flow.raw) == dumps(stepwise.flow.raw)
    kinds = {c.kind for c in pipeline.flow_result.changes}
    assert {"create-context", "rewrite-property", "replace-component", "set-scheduling"} <= kinds
    assert sorted(t.source.name for t in pipeline.templates) == [
        "Removed Components Template",
        "Template Conversion Template",
    ]
    # A variable reference rewritten in step 1 survives the property rename of step 2.
    listen = next(
        c for c in pipeline.flow_result.flow.components() if c.name == "Listen on variable port"
    )
    assert listen.properties["Listening Port"] == "#{port}"


def test_pipeline_matches_golden(catalog: Catalog, update_golden: bool) -> None:
    pipeline = migrate_all(load(FIXTURES / "flow.json.gz"), catalog)
    actual = dumps(pipeline.flow_result.flow.raw)
    changes = render_changes(pipeline.flow_result.changes)
    if update_golden:
        GOLDEN.mkdir(parents=True, exist_ok=True)
        (GOLDEN / "flow.flow.json").write_text(actual, encoding="utf-8", newline="\n")
        (GOLDEN / "flow.changes.json").write_text(changes, encoding="utf-8", newline="\n")
        pytest.skip("golden files updated")
    assert actual == (GOLDEN / "flow.flow.json").read_text(encoding="utf-8")
    assert changes == (GOLDEN / "flow.changes.json").read_text(encoding="utf-8")


def test_definition_input_has_no_templates(catalog: Catalog) -> None:
    pipeline = migrate_all(load(FIXTURES / "definitions" / "variables.json"), catalog)
    assert pipeline.templates == []
    assert any(c.kind == "create-context" for c in pipeline.flow_result.changes)


def test_cli_writes_everything_into_the_directory(tmp_path: Path) -> None:
    source = FIXTURES / "flow.json.gz"
    out = tmp_path / "migrated"
    result = runner.invoke(app, ["migrate", "all", str(source), "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in out.iterdir()) == [
        "changes.json",
        "flow.json.gz",
        "report.md",
        "templates",
    ]
    assert sorted(p.name for p in (out / "templates").iterdir()) == [
        "removed-components-template.json",
        "removed-components-template.report.md",
        "template-conversion-template.json",
        "template-conversion-template.report.md",
    ]
    changes = json.loads((out / "changes.json").read_text(encoding="utf-8"))
    assert {c["kind"] for c in changes} >= {"create-context", "replace-component", "set-scheduling"}
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "migrate all" in report and "templates/" in report
    # the migrated flow is valid analyzer input and the blockers that remain are the documented ones
    check = runner.invoke(
        app, ["analyze", str(out / "flow.json.gz"), "-f", "json", "--fail-on", "never"]
    )
    assert check.exit_code == 0
    remaining = json.loads(check.output)["summary"]["by_rule"]
    assert "NIFI2-EVENT-DRIVEN" not in remaining
    assert "NIFI2-VARIABLES-DEFINED" in remaining  # variables with manual references stay
    dry = runner.invoke(
        app, ["migrate", "all", str(source), "-o", str(tmp_path / "dry"), "--dry-run"]
    )
    assert dry.exit_code == 0 and not (tmp_path / "dry").exists()
