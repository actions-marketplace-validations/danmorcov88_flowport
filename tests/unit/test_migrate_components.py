"""Component replacements: golden files on the fixtures, edge cases on synthetic flows."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from flowport.catalog import Catalog, load_catalog
from flowport.loaders import load, load_flow_document
from flowport.reports import build_report, render_json
from flowport.rules import Finding, analyze
from flowport.transforms import MigrationResult, render_changes
from flowport.transforms.components import migrate_components
from flowport.writers import dumps
from tests.unit.synthetic import STD, document, group, processor

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nifi-1.28.1"
GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "nifi-1.28.1" / "migrate-components"
CASES = [
    ("flow", FIXTURES / "flow.json.gz"),
    ("definition-replacements", FIXTURES / "definitions" / "replacements.json"),
    ("definition-scheduling", FIXTURES / "definitions" / "scheduling.json"),
]


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog()


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
    result = migrate_components(flow, catalog)
    assert flow.raw == before, "the loaded input must not be modified"
    findings = sorted([*analyze(result.flow, catalog), *result.findings], key=Finding.sort_key)
    report = build_report(result.flow, findings, catalog, None)
    del report["tool"]
    _compare(dumps(result.flow.raw), GOLDEN / f"{name}.flow.json", update_golden)
    _compare(render_changes(result.changes), GOLDEN / f"{name}.changes.json", update_golden)
    _compare(render_json(report), GOLDEN / f"{name}.report.json", update_golden)
    if update_golden:
        pytest.skip("golden files updated")


def test_replacements_fixture_end_to_end(catalog: Catalog) -> None:
    result = migrate_components(load(FIXTURES / "definitions" / "replacements.json"), catalog)
    by_name = {c.name: c for c in result.flow.components()}
    assert by_name["Fetch"].type == "org.apache.nifi.processors.standard.InvokeHTTP"
    assert by_name["Fetch"].bundle.version == "2.12.0"
    assert by_name["Fetch"].properties["HTTP URL"] == "https://example.org/data.json"
    assert by_name["Fetch"].properties["HTTP Method"] == "GET"
    assert by_name["Fetch"].properties["Response Redirects Enabled"] == "True"
    assert (
        "URL" not in by_name["Fetch"].properties and "Filename" not in by_name["Fetch"].properties
    )
    assert by_name["Post"].properties["Request Content-Encoding"] == "GZIP"
    assert by_name["Post"].properties["Request Chunked Transfer-Encoding Enabled"] == "true"
    assert by_name["Post packaged"].type == STD + "PostHTTP", "guarded replacement not applied"
    assert by_name["Encode"].properties == {"Mode": "Decode", "Encoding": "base64"}
    assert by_name["Jolt"].properties["Jolt Specification"] == '{"a": "b"}'
    assert by_name["Cache server"].type.endswith(".MapCacheServer")
    assert by_name["Cache server"].properties["Maximum Read Size"] == "1 MB"
    # Processors keep pointing at the renamed service instance.
    reference = by_name["Dedupe"].properties["Distributed Cache Service"]
    assert reference == by_name["Cache client"].versioned_id
    # Relationships on connections and auto-terminations.
    group = result.flow.root
    selected = {
        (c.raw["source"]["name"], c.raw["destination"]["name"]): c.relationships
        for c in group.connections
    }
    assert selected[("Fetch", "Encode")] == ["Response"]
    assert selected[("Post", "Sent")] == ["Original"]
    assert selected[("Post", "Failed")] == ["Failure", "Retry", "No Retry"]
    assert selected[("Encode", "Dedupe")] == ["success"]
    assert by_name["Fetch"].raw["autoTerminatedRelationships"] == [
        "Original",
        "Retry",
        "No Retry",
        "Failure",
    ]
    assert by_name["Post"].raw["autoTerminatedRelationships"] == ["Response"]
    # Property descriptors follow the properties (sensitivity, service references).
    descriptors = by_name["Fetch"].raw["propertyDescriptors"]
    assert descriptors["HTTP URL"] == {
        "name": "HTTP URL",
        "displayName": "HTTP URL",
        "identifiesControllerService": False,
        "sensitive": False,
    }
    assert descriptors["HTTP Method"]["name"] == "HTTP Method"
    assert "URL" not in descriptors
    rule_ids = [f.rule_id for f in result.findings]
    assert rule_ids.count("NIFI2-COMPONENT-REPLACED") == 9
    assert rule_ids.count("NIFI2-REPLACEMENT-SKIPPED") == 1
    dropped = {
        (f.location.name, f.details["property"])
        for f in result.findings
        if f.rule_id == "NIFI2-REPLACED-PROPERTY-DROPPED"
    }
    assert dropped == {("Fetch", "Filename"), ("Fetch", "Accept Content-Type")}
    # Nothing left for the analyzer to say about the replaced types.
    post = analyze(result.flow, catalog)
    assert {f.location.name for f in post if f.rule_id == "NIFI2-REMOVED-COMPONENT"} == {
        "Post packaged"
    }
    assert not [f for f in post if f.rule_id == "NIFI2-UNKNOWN-COMPONENT"]


def test_same_input_gives_identical_output(catalog: Catalog) -> None:
    path = FIXTURES / "flow.json.gz"
    first = migrate_components(load(path), catalog)
    second = migrate_components(load(path), catalog)
    assert dumps(first.flow.raw) == dumps(second.flow.raw)
    assert render_changes(first.changes) == render_changes(second.changes)


# -- synthetic edge cases -----------------------------------------------------


def migrate(catalog: Catalog, root: dict[str, Any], **extra: Any) -> MigrationResult:
    return migrate_flow(catalog, document(root, **extra))


def migrate_flow(catalog: Catalog, doc: dict[str, Any]) -> MigrationResult:
    return migrate_components(load_flow_document(doc), catalog)


def test_event_driven_becomes_timer_driven(catalog: Catalog) -> None:
    root = group(
        "root",
        [
            processor("e", STD + "GenerateFlowFile", scheduling="EVENT_DRIVEN", period="5 sec"),
            processor("t", STD + "GenerateFlowFile", scheduling="TIMER_DRIVEN", period="5 sec"),
        ],
    )
    result = migrate(catalog, root)
    by_name = {c.name: c for c in result.flow.components()}
    assert by_name["e"].scheduling_strategy == "TIMER_DRIVEN"
    assert by_name["e"].scheduling_period == "0 sec"
    assert by_name["t"].scheduling_period == "5 sec"
    assert [(c.kind, c.component_name) for c in result.changes] == [("set-scheduling", "e")]
    assert [f.rule_id for f in result.findings] == ["NIFI2-SCHEDULING-CHANGED"]
    assert not [f for f in analyze(result.flow, catalog) if f.rule_id == "NIFI2-EVENT-DRIVEN"]


def test_dynamic_properties_survive_when_the_target_supports_them(catalog: Catalog) -> None:
    fetch = processor(
        "fetch", STD + "GetHTTP", {"URL": "http://x", "Filename": "f", "X-Trace": "on"}
    )
    result = migrate(catalog, group("root", [fetch]))
    fetched = next(c for c in result.flow.components())
    assert fetched.properties["X-Trace"] == "on"  # InvokeHTTP takes dynamic properties (headers)
    assert fetched.properties["HTTP URL"] == "http://x"


def test_dropped_property_is_silent_at_its_default(catalog: Catalog) -> None:
    quiet = processor("quiet", STD + "PostHTTP", {"URL": "http://x", "Max Batch Size": "100 MB"})
    loud = processor("loud", STD + "PostHTTP", {"URL": "http://x", "Max Batch Size": "5 MB"})
    result = migrate(catalog, group("root", [quiet, loud]))
    dropped = {
        f.location.name for f in result.findings if f.rule_id == "NIFI2-REPLACED-PROPERTY-DROPPED"
    }
    assert dropped == {"loud"}
    assert {c.component_name for c in result.changes if c.kind == "drop-property"} == {
        "quiet",
        "loud",
    }


def test_guard_condition_is_case_insensitive_and_reported(catalog: Catalog) -> None:
    packaged = processor("p", STD + "PostHTTP", {"URL": "http://x", "Send as FlowFile": "True"})
    result = migrate(catalog, group("root", [packaged]))
    assert result.changes == []
    finding = result.findings[0]
    assert finding.rule_id == "NIFI2-REPLACEMENT-SKIPPED"
    assert finding.severity.value == "MANUAL"
    assert "MergeContent" in finding.message
    assert next(c for c in result.flow.components()).type == STD + "PostHTTP"


def test_connection_matching_uses_instance_or_versioned_ids(catalog: Catalog) -> None:
    fetch = processor("fetch", STD + "GetHTTP", {"URL": "http://x", "Filename": "f"})
    log = processor("log", STD + "LogAttribute")
    root = group("root", [fetch, log])
    root["connections"] = [
        {
            "identifier": "c-1",
            "instanceIdentifier": "inst-c-1",
            "name": "",
            "source": {"id": "id-fetch", "type": "PROCESSOR", "name": "fetch"},
            "destination": {"id": "id-log", "type": "PROCESSOR", "name": "log"},
            "selectedRelationships": ["success"],
            "componentType": "CONNECTION",
        }
    ]
    result = migrate(catalog, root)
    assert result.flow.root.connections[0].relationships == ["Response"]
    change = next(c for c in result.changes if c.kind == "map-relationship")
    assert change.component_name == "fetch -> log"
    assert (change.old, change.new) == ("success", "Response")


def test_components_outside_process_groups_are_replaced_too(catalog: Catalog) -> None:
    service = {
        "identifier": "s1",
        "instanceIdentifier": "inst-s1",
        "name": "shared cache",
        "type": "org.apache.nifi.distributed.cache.client.DistributedMapCacheClientService",
        "bundle": {
            "group": "org.apache.nifi",
            "artifact": "nifi-distributed-cache-services-nar",
            "version": "1.28.1",
        },
        "properties": {"Server Hostname": "cache"},
        "componentType": "CONTROLLER_SERVICE",
    }
    result = migrate(catalog, group("root"), controllerServices=[service])
    replaced = result.flow.controller_services[0]
    assert replaced.type.endswith(".MapCacheClientService")
    assert replaced.bundle.artifact == "nifi-distributed-cache-services-nar"
    assert replaced.properties == {"Server Hostname": "cache"}


def test_kind_mismatch_is_not_replaced(catalog: Catalog) -> None:
    # A controller service whose type name matches a processor replacement is left alone.
    odd = {
        "identifier": "odd",
        "instanceIdentifier": "inst-odd",
        "name": "odd",
        "type": STD + "GetHTTP",
        "bundle": {
            "group": "org.apache.nifi",
            "artifact": "nifi-standard-nar",
            "version": "1.28.1",
        },
        "properties": {},
        "componentType": "CONTROLLER_SERVICE",
    }
    root = group("root")
    root["controllerServices"] = [odd]
    result = migrate(catalog, root)
    assert result.changes == []


def test_sensitive_property_keeps_its_descriptor_when_renamed(catalog: Catalog) -> None:
    fetch = processor("fetch", STD + "GetHTTP", {"URL": "http://x", "Password": "enc{c2VjcmV0}"})
    fetch["propertyDescriptors"] = {
        "URL": {"name": "URL", "identifiesControllerService": False, "sensitive": False},
        "Password": {"name": "Password", "identifiesControllerService": False, "sensitive": True},
    }
    result = migrate(catalog, group("root", [fetch]))
    replaced = next(c for c in result.flow.components())
    assert replaced.properties["Request Password"] == "enc{c2VjcmV0}"
    assert replaced.raw["propertyDescriptors"]["Request Password"]["sensitive"] is True
    assert "Password" not in replaced.raw["propertyDescriptors"]
