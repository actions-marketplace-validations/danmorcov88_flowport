"""Variables -> parameter contexts: golden files on the fixtures, edge cases on synthetic flows."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from flowport.catalog import Catalog, load_catalog
from flowport.loaders import load, load_definition_document, load_flow_document
from flowport.model import Flow
from flowport.reports import build_report, render_json
from flowport.rules import Finding, analyze
from flowport.transforms import Change, MigrationResult, render_changes
from flowport.transforms.variables import (
    instance_identifier,
    migrate_variables,
    versioned_identifier,
)
from flowport.writers import dumps
from tests.unit.synthetic import STD, document, group, processor

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nifi-1.28.1"
GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "nifi-1.28.1" / "migrate-variables"

CASES = [
    ("flow", FIXTURES / "flow.json.gz"),
    ("definition-variables", FIXTURES / "definitions" / "variables.json"),
    ("definition-existing-context", FIXTURES / "definitions" / "existing-context.json"),
]


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog()


def _post_report(result: MigrationResult, catalog: Catalog) -> dict[str, Any]:
    findings = sorted([*analyze(result.flow, catalog), *result.findings], key=Finding.sort_key)
    report = build_report(result.flow, findings, catalog, None)
    del report["tool"]
    return report


def _compare(actual: str, golden: Path, update_golden: bool) -> None:
    if update_golden:
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(actual, encoding="utf-8", newline="\n")
        return
    assert golden.exists(), f"missing golden file {golden}; run pytest --update-golden"
    expected = golden.read_text(encoding="utf-8")
    assert json.loads(actual) == json.loads(expected)
    assert actual == expected, "output differs from golden file (formatting)"


@pytest.mark.parametrize(("name", "path"), CASES, ids=[c[0] for c in CASES])
def test_migration_matches_golden(
    name: str, path: Path, catalog: Catalog, update_golden: bool
) -> None:
    flow = load(path)
    before = copy.deepcopy(flow.raw)
    result = migrate_variables(flow, catalog)
    assert flow.raw == before, "the loaded input must not be modified"
    _compare(dumps(result.flow.raw), GOLDEN / f"{name}.flow.json", update_golden)
    _compare(render_changes(result.changes), GOLDEN / f"{name}.changes.json", update_golden)
    _compare(
        render_json(_post_report(result, catalog)), GOLDEN / f"{name}.report.json", update_golden
    )
    if update_golden:
        pytest.skip("golden files updated")


def test_fixture_flow_covers_every_case(catalog: Catalog) -> None:
    result = migrate_variables(load(FIXTURES / "flow.json.gz"), catalog)
    kinds = {(c.kind, c.path, c.property, c.context) for c in result.changes}
    # 1. contexts per group, child inherits parent, grandchild assigned the nearest context
    assert ("create-context", "/NiFi Flow/Variables", None, "Variables Variables") in kinds
    assert ("create-context", "/NiFi Flow/Variables/Child", None, "Child Variables") in kinds
    assert (
        "assign-context",
        "/NiFi Flow/Variables/Child/Grandchild",
        None,
        "Child Variables",
    ) in kinds
    contexts = {c["name"]: c for c in result.flow.raw["parameterContexts"]}
    assert contexts["Child Variables"]["inheritedParameterContexts"] == ["Variables Variables"]
    assert contexts["Variables Variables"]["inheritedParameterContexts"] == []
    # 2. existing context with a colliding name: finding, group untouched
    assert [f.rule_id for f in result.findings] == ["NIFI2-VARIABLE-PARAMETER-COLLISION"]
    assert not any(c.path == "/NiFi Flow/Existing Context" for c in result.changes)
    assert [p["name"] for p in contexts["Fixture Context"]["parameters"]] == ["host", "timeout"]
    # 3./4./5. only VARIABLE_REGISTRY-scope references are rewritten, inside larger values too
    rewrites = {
        (c.component_name, c.property): c.new
        for c in result.changes
        if c.kind == "rewrite-property"
    }
    assert rewrites == {
        ("Generate with host text", "generate-ff-custom-text"): "host=#{host} port=#{port}",
        ("Listen on variable port", "Listening Port"): "#{port}",
        ("Generate with parent host", "generate-ff-custom-text"): "#{host}",
        ("Get from variable path", "Input Directory"): "#{path}",
        ("Generate with inherited host", "generate-ff-custom-text"): "#{host}",
        ("Get from inherited path", "Input Directory"): "#{path}/#{shared}",
    }
    untouched = {
        p["name"]: p["properties"]
        for g in result.flow.groups()
        for p in g.raw["processors"]
        if p["name"]
        in {
            "Build URL attribute",
            "Log literal dollar text",
            "Tail with function",
            "Put to shadowed directory",
        }
    }
    assert untouched["Build URL attribute"]["url"] == "http://${host}:${port}/api"
    assert untouched["Log literal dollar text"]["Attributes to Log"] == "${host}"
    assert untouched["Tail with function"]["File to Tail"] == "${shared:toUpper()}.log"
    assert untouched["Put to shadowed directory"]["Directory"] == "/tmp/${host}/out"
    # 6./7. invalid names and unused variables do not become parameters
    assert [p["name"] for p in contexts["Variables Variables"]["parameters"]] == [
        "host",
        "port",
        "shared",
    ]
    # variables stay until every evaluated reference is rewritten
    variables = {g.path: g.variables for g in result.flow.groups() if g.variables}
    assert variables == {
        "/NiFi Flow/Variables": {
            "unused.var": "never referenced",
            "shared": "from-top",
            "bad name!": "characters not allowed in parameter names",
            "port": "8080",
            "host": "example.org",
        },
        "/NiFi Flow/Existing Context": {"retries": "3", "host": "variable.example.org"},
    }
    assert result.flow.raw["rootGroup"]["processGroups"][0]["processGroups"][0]["variables"] == {}


def test_generated_identifiers_are_stable_and_nifi_shaped() -> None:
    instance = instance_identifier("a961df39-01a0-1000-7275-0a4488a407b2")
    assert instance == instance_identifier("a961df39-01a0-1000-7275-0a4488a407b2")
    assert instance[14] == "5"
    versioned = versioned_identifier(instance)
    assert versioned[14] == "3"
    # NiFi's own derivation, checked against a context from the fixture
    assert (
        versioned_identifier("a961e1ed-01a0-1000-8f0c-b51b24bf881d")
        == "df70a456-8442-36a2-ba0c-75e0e15ef1f5"
    )


def test_same_input_gives_identical_output(catalog: Catalog) -> None:
    path = FIXTURES / "flow.json.gz"
    first = migrate_variables(load(path), catalog)
    second = migrate_variables(load(path), catalog)
    assert dumps(first.flow.raw) == dumps(second.flow.raw)
    assert render_changes(first.changes) == render_changes(second.changes)


# -- synthetic edge cases -----------------------------------------------------


def migrate(catalog: Catalog, root: dict[str, Any], **extra: Any) -> MigrationResult:
    return migrate_variables(load_flow_document(document(root, **extra)), catalog)


def context(
    name: str, parameters: dict[str, str], inherits: list[str] | None = None
) -> dict[str, Any]:
    return {
        "identifier": f"id-{name}",
        "instanceIdentifier": f"inst-{name}",
        "name": name,
        "parameters": [
            {"name": k, "sensitive": False, "provided": False, "value": v}
            for k, v in parameters.items()
        ],
        "inheritedParameterContexts": inherits or [],
        "description": "",
        "componentType": "PARAMETER_CONTEXT",
    }


def properties(result: MigrationResult, processor_name: str) -> dict[str, str | None]:
    for component in result.flow.components():
        if component.name == processor_name:
            return component.properties
    raise AssertionError(processor_name)


def changes_of(result: MigrationResult, kind: str) -> list[Change]:
    return [c for c in result.changes if c.kind == kind]


def test_rewrites_only_the_reference_span(catalog: Catalog) -> None:
    root = group(
        "root",
        [
            processor(
                "p",
                STD + "GetFile",
                {
                    "Input Directory": "/a/${ 'dir' }/${dir}/$${dir}/${dir:trim()}/${'dir'}",
                    "File Filter": "${dir}${dir}",  # no Expression Language support
                    "Path Filter": None,
                },
            )
        ],
        variables={"dir": "in"},
    )
    result = migrate(catalog, root)
    assert (
        properties(result, "p")["Input Directory"]
        == "/a/#{dir}/#{dir}/$${dir}/${dir:trim()}/#{dir}"
    )
    assert properties(result, "p")["File Filter"] == "${dir}${dir}"
    assert len(changes_of(result, "rewrite-property")) == 1
    # the function reference keeps the variable alive
    assert result.flow.root.variables == {"dir": "in"}
    assert changes_of(result, "remove-variable") == []


def test_context_name_is_deduplicated(catalog: Catalog) -> None:
    root = group(
        "root", [processor("p", STD + "GetFile", {"Input Directory": "${d}"})], variables={"d": "x"}
    )
    result = migrate(catalog, root, parameterContexts=[context("root Variables", {"other": "1"})])
    names = [c["name"] for c in result.flow.raw["parameterContexts"]]
    assert names == ["root Variables", "root Variables 2"]
    assert result.flow.root.parameter_context_name == "root Variables 2"
    assert result.flow.root.variables == {}


def test_existing_context_gets_parameters_when_nothing_collides(catalog: Catalog) -> None:
    root = group(
        "root", [processor("p", STD + "GetFile", {"Input Directory": "${d}"})], variables={"d": "x"}
    )
    root["parameterContextName"] = "Ctx"
    result = migrate(catalog, root, parameterContexts=[context("Ctx", {"other": "1"})])
    assert changes_of(result, "create-context") == []
    assert [c.context for c in changes_of(result, "add-parameter")] == ["Ctx"]
    parameters = result.flow.raw["parameterContexts"][0]["parameters"]
    assert [p["name"] for p in parameters] == ["other", "d"]
    assert parameters[1] == {
        "name": "d",
        "description": "Converted from variable 'd' of process group /root",
        "sensitive": False,
        "provided": False,
        "value": "x",
    }
    assert properties(result, "p")["Input Directory"] == "#{d}"
    assert result.findings == []


def test_collision_with_an_inherited_parameter_blocks_the_group(catalog: Catalog) -> None:
    root = group(
        "root",
        [processor("p", STD + "GetFile", {"Input Directory": "${d}/${e}"})],
        variables={"d": "x", "e": "y"},
    )
    root["parameterContextName"] = "Ctx"
    contexts = [context("Base", {"d": "from-base"}), context("Ctx", {}, inherits=["Base"])]
    result = migrate(catalog, root, parameterContexts=contexts)
    assert result.changes == []
    assert [(f.rule_id, f.details["variable"], f.details["context"]) for f in result.findings] == [
        ("NIFI2-VARIABLE-PARAMETER-COLLISION", "d", "Ctx")
    ]
    assert result.flow.root.variables == {"d": "x", "e": "y"}
    assert properties(result, "p")["Input Directory"] == "${d}/${e}"


def test_existing_context_between_definition_and_reference(catalog: Catalog) -> None:
    leaf = group("leaf", [processor("p", STD + "GetFile", {"Input Directory": "${d}"})])
    middle = group("middle", groups=[leaf])
    middle["parameterContextName"] = "Middle Ctx"
    root = group("root", groups=[middle], variables={"d": "x"})
    result = migrate(catalog, root, parameterContexts=[context("Middle Ctx", {})])
    assert [f.rule_id for f in result.findings] == ["NIFI2-VARIABLE-REFERENCE-NOT-VISIBLE"]
    finding = result.findings[0]
    assert finding.details["reason"] == "does not inherit it"
    assert finding.details["parameter_context"] == "root Variables"
    assert finding.details["context"] == "Middle Ctx"
    assert "does not inherit it" in finding.message
    assert properties(result, "p")["Input Directory"] == "${d}"
    assert result.flow.root.variables == {"d": "x"}  # kept: a reference still needs it
    assert result.flow.root.parameter_context_name == "root Variables"
    leaf_group = next(g for g in result.flow.groups() if g.name == "leaf")
    assert leaf_group.parameter_context_name is None


def test_reference_shadowed_by_the_groups_own_context(catalog: Catalog) -> None:
    child = group("child", [processor("p", STD + "GetFile", {"Input Directory": "${d}"})])
    child["parameterContextName"] = "Child Ctx"
    root = group("root", groups=[child], variables={"d": "x"})
    result = migrate(catalog, root, parameterContexts=[context("Child Ctx", {"d": "other"})])
    assert [f.rule_id for f in result.findings] == ["NIFI2-VARIABLE-REFERENCE-NOT-VISIBLE"]
    assert (
        result.findings[0].details["reason"]
        == "already resolves #{d} to a parameter of 'Child Ctx'"
    )
    assert properties(result, "p")["Input Directory"] == "${d}"


def test_group_without_variables_is_assigned_the_nearest_context(catalog: Catalog) -> None:
    leaf = group("leaf", [processor("p", STD + "GetFile", {"Input Directory": "${d}"})])
    middle = group("middle", groups=[leaf])
    root = group("root", groups=[middle], variables={"d": "x"})
    result = migrate(catalog, root)
    assigned = {c.path: c.context for c in changes_of(result, "assign-context")}
    assert assigned == {"/root": "root Variables", "/root/middle/leaf": "root Variables"}
    assert properties(result, "p")["Input Directory"] == "#{d}"


def test_keep_unused_converts_everything_with_a_valid_name(catalog: Catalog) -> None:
    root = group("root", variables={"unused": "1", "bad/name": "2"})
    flow = load_flow_document(document(root))
    assert migrate_variables(flow, catalog).changes == []
    result = migrate_variables(flow, catalog, keep_unused=True)
    assert [c.property for c in changes_of(result, "add-parameter")] == ["unused"]
    assert [c.property for c in changes_of(result, "remove-variable")] == ["unused"]
    assert result.flow.root.variables == {"bad/name": "2"}


def test_attribute_scope_reference_creates_the_parameter_but_keeps_the_variable(
    catalog: Catalog,
) -> None:
    root = group(
        "root", [processor("p", STD + "PutFile", {"Directory": "${d}"})], variables={"d": "x"}
    )
    result = migrate(catalog, root)
    assert [c.property for c in changes_of(result, "add-parameter")] == ["d"]
    assert changes_of(result, "rewrite-property") == []
    assert result.flow.root.variables == {"d": "x"}


def test_text_in_a_property_without_el_does_not_count(catalog: Catalog) -> None:
    root = group(
        "root",
        [processor("p", STD + "LogAttribute", {"Attributes to Log": "${d}"})],
        variables={"d": "x"},
    )
    result = migrate(catalog, root)
    assert result.changes == []


def test_definition_input_writes_contexts_as_a_dict(catalog: Catalog) -> None:
    child = group("child", [processor("p", STD + "GetFile", {"Input Directory": "${d}"})])
    root = group("root", groups=[child], variables={"d": "x"})
    doc = {"flowContents": root, "parameterContexts": {}, "flowEncodingVersion": "1.0"}
    result = migrate_variables(load_definition_document(doc), catalog)
    assert result.flow.raw["parameterContexts"] == {
        "root Variables": {
            "name": "root Variables",
            "parameters": [
                {
                    "name": "d",
                    "description": "Converted from variable 'd' of process group /root",
                    "sensitive": False,
                    "provided": False,
                    "value": "x",
                }
            ],
            "inheritedParameterContexts": [],
            "description": "Parameters converted from the variables of process group /root",
            "componentType": "PARAMETER_CONTEXT",
        }
    }
    assert result.flow.raw["flowContents"]["parameterContextName"] == "root Variables"
    assert (
        result.flow.raw["flowContents"]["processGroups"][0]["parameterContextName"]
        == "root Variables"
    )
    assert doc["parameterContexts"] == {}  # input document untouched


def test_flow_without_parameter_contexts_key(catalog: Catalog) -> None:
    root = group(
        "root", [processor("p", STD + "GetFile", {"Input Directory": "${d}"})], variables={"d": "x"}
    )
    flow: Flow = load_flow_document(document(root))
    result = migrate_variables(flow, catalog)
    assert [c["name"] for c in result.flow.raw["parameterContexts"]] == ["root Variables"]
    assert "parameterContexts" not in flow.raw
