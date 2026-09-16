"""Templates -> flow definitions: readers, converter, goldens, structural comparison."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from flowport.catalog import Catalog, load_catalog
from flowport.loaders import LoadError, load
from flowport.loaders.templates import TemplateSource, parse_template_xml, templates_in_flow
from flowport.reports import build_report, render_json
from flowport.transforms.templates import (
    COUNT_KEYS,
    convert_and_analyze,
    convert_template,
    slugify,
)
from flowport.writers import dumps

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nifi-1.28.1"
GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "nifi-1.28.1" / "migrate-templates"
TEMPLATES = sorted((FIXTURES / "templates").glob("*.xml"))


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog()


@pytest.fixture(scope="module")
def flow_templates() -> dict[str, TemplateSource]:
    return {t.name: t for t in templates_in_flow(load(FIXTURES / "flow.json.gz").raw)}


def _compare(actual: str, golden: Path, update_golden: bool) -> None:
    if update_golden:
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(actual, encoding="utf-8", newline="\n")
        return
    assert golden.exists(), f"missing golden file {golden}; run pytest --update-golden"
    expected = golden.read_text(encoding="utf-8")
    assert json.loads(actual) == json.loads(expected)
    assert actual == expected, "output differs from golden file (formatting)"


@pytest.mark.parametrize("xml_path", TEMPLATES, ids=[p.stem for p in TEMPLATES])
def test_conversion_matches_golden(xml_path: Path, catalog: Catalog, update_golden: bool) -> None:
    source = parse_template_xml(xml_path.read_bytes())
    snippet_before = copy.deepcopy(source.snippet)
    converted = convert_and_analyze(source, catalog)
    assert source.snippet == snippet_before, "the parsed template must not be modified"
    _compare(dumps(converted.document), GOLDEN / f"{xml_path.stem}.json", update_golden)
    report = build_report(converted.flow, converted.findings, catalog, None)
    del report["tool"]
    _compare(render_json(report), GOLDEN / f"{xml_path.stem}.report.json", update_golden)
    if update_golden:
        pytest.skip("golden files updated")


@pytest.mark.parametrize("xml_path", TEMPLATES, ids=[p.stem for p in TEMPLATES])
def test_xml_and_flow_json_templates_convert_identically(
    xml_path: Path, flow_templates: dict[str, TemplateSource]
) -> None:
    from_xml = parse_template_xml(xml_path.read_bytes())
    from_flow = flow_templates[from_xml.name]
    assert from_xml.encoding_version == from_flow.encoding_version == "1.3"
    assert from_xml.group_id == from_flow.group_id
    assert convert_template(from_xml) == convert_template(from_flow)


# -- structural comparison with NiFi's own flow definition of the same group ---


def _walk_groups(group: dict[str, Any]) -> list[dict[str, Any]]:
    found = [group]
    for child in group.get("processGroups") or []:
        found.extend(_walk_groups(child))
    return found


def _counts(group: dict[str, Any]) -> dict[str, int]:
    return {key: sum(len(g.get(key) or []) for g in _walk_groups(group)) for key in COUNT_KEYS}


def _by_name(group: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for g in _walk_groups(group) for c in g.get(key) or []}


def _connection_ends(group: dict[str, Any]) -> set[tuple[str, str, str, str, tuple[str, ...]]]:
    return {
        (
            c["source"]["name"],
            c["source"]["type"],
            c["destination"]["name"],
            c["destination"]["type"],
            tuple(c["selectedRelationships"]),
        )
        for g in _walk_groups(group)
        for c in g["connections"]
    }


def test_converted_template_matches_nifi_definition_of_the_same_group(
    flow_templates: dict[str, TemplateSource],
) -> None:
    converted = convert_template(flow_templates["Template Conversion Template"])["flowContents"]
    reference = json.loads(
        (FIXTURES / "definitions" / "template-conversion.json").read_text(encoding="utf-8")
    )["flowContents"]

    assert _counts(converted) == _counts(reference)
    assert _connection_ends(converted) == _connection_ends(reference)

    for key in ("processors", "controllerServices"):
        ours, theirs = _by_name(converted, key), _by_name(reference, key)
        assert set(ours) == set(theirs)
        for name, component in ours.items():
            expected = theirs[name]
            assert component["type"] == expected["type"]
            assert component["bundle"] == expected["bundle"]
            # Templates only carry set values; NiFi's export also lists unset ones as null.
            set_values = {k: v for k, v in expected["properties"].items() if v is not None}
            service_refs = {
                k
                for k, d in expected["propertyDescriptors"].items()
                if d["identifiesControllerService"]
            }
            plain = {k: v for k, v in component["properties"].items() if k not in service_refs}
            assert plain == {k: v for k, v in set_values.items() if k not in service_refs}, name
            assert set(component["propertyDescriptors"]) == set(expected["propertyDescriptors"])
            for prop, descriptor in component["propertyDescriptors"].items():
                theirs_d = expected["propertyDescriptors"][prop]
                assert (
                    descriptor["identifiesControllerService"]
                    == theirs_d["identifiesControllerService"]
                )
            for field in ("scheduledState", "bulletinLevel"):
                assert component.get(field) == expected.get(field), (name, field)
        if key == "processors":
            for name, component in ours.items():
                expected = theirs[name]
                for field in (
                    "schedulingPeriod",
                    "schedulingStrategy",
                    "executionNode",
                    "penaltyDuration",
                    "yieldDuration",
                    "runDurationMillis",
                    "concurrentlySchedulableTaskCount",
                    "autoTerminatedRelationships",
                    "retryCount",
                    "retriedRelationships",
                    "backoffMechanism",
                    "maxBackoffPeriod",
                    "comments",
                    "position",
                    "style",
                ):
                    assert component[field] == expected[field], (name, field)

    # Service references point at the converted services (template ids).
    services = {s["identifier"] for s in converted["controllerServices"]}
    convert = _by_name(converted, "processors")["Convert"]
    assert {
        convert["properties"]["record-reader"],
        convert["properties"]["record-writer"],
    } <= services

    ours_conn = {c.get("name"): c for g in _walk_groups(converted) for c in g["connections"]}
    theirs_conn = {c.get("name"): c for g in _walk_groups(reference) for c in g["connections"]}
    for field in (
        "backPressureObjectThreshold",
        "backPressureDataSizeThreshold",
        "flowFileExpiration",
        "prioritizers",
        "bends",
        "labelIndex",
        "zIndex",
        "loadBalanceStrategy",
        "loadBalanceCompression",
    ):
        assert ours_conn["generated"][field] == theirs_conn["generated"][field], field

    for key in ("labels", "funnels", "inputPorts", "outputPorts", "remoteProcessGroups"):
        ours_list = [c for g in _walk_groups(converted) for c in g[key]]
        theirs_list = [c for g in _walk_groups(reference) for c in g[key]]
        assert len(ours_list) == len(theirs_list) == 1, key
        for field in (
            "position",
            "label",
            "width",
            "height",
            "type",
            "targetUris",
            "transportProtocol",
        ):
            if field in theirs_list[0]:
                assert ours_list[0][field] == theirs_list[0][field], (key, field)
    remote = next(c for g in _walk_groups(converted) for c in g["remoteProcessGroups"])
    assert [p["name"] for p in remote["inputPorts"]] == ["Remote In"]
    assert remote["inputPorts"][0]["componentType"] == "REMOTE_INPUT_PORT"
    child = converted["processGroups"][0]
    assert child["name"] == "Sink" and child["groupIdentifier"] == converted["identifier"]
    assert child["flowFileConcurrency"] == reference["processGroups"][0]["flowFileConcurrency"]
    assert converted["variables"] == {}


# -- reader -----------------------------------------------------------------


def test_xml_reader_handles_lists_maps_and_empties() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<template encoding-version="1.2">
  <description/>
  <groupId>g</groupId>
  <name>Tiny</name>
  <snippet>
    <processors>
      <id>p1</id>
      <parentGroupId>g</parentGroupId>
      <name>Only</name>
      <type>org.apache.nifi.processors.standard.LogAttribute</type>
      <bundle><group>org.apache.nifi</group><artifact>nifi-standard-nar</artifact><version>1.28.1</version></bundle>
      <state>DISABLED</state>
      <style/>
      <relationships><autoTerminate>true</autoTerminate><name>success</name></relationships>
      <config>
        <properties>
          <entry><key>Log Level</key><value>warn</value></entry>
          <entry><key>Log prefix</key></entry>
        </properties>
        <descriptors>
          <entry><key>Log Level</key><value><name>Log Level</name></value></entry>
          <entry><key>Log prefix</key>
            <value><name>Log prefix</name><sensitive>true</sensitive></value></entry>
        </descriptors>
        <autoTerminatedRelationships>success</autoTerminatedRelationships>
        <schedulingPeriod>0 sec</schedulingPeriod>
        <schedulingStrategy>TIMER_DRIVEN</schedulingStrategy>
        <runDurationMillis>25</runDurationMillis>
        <concurrentlySchedulableTaskCount>3</concurrentlySchedulableTaskCount>
        <comments></comments>
      </config>
    </processors>
    <connections>
      <id>c1</id>
      <parentGroupId>g</parentGroupId>
      <source><id>p1</id><type>PROCESSOR</type><groupId>g</groupId></source>
      <destination><id>p1</id><type>PROCESSOR</type><groupId>g</groupId></destination>
      <selectedRelationships>success</selectedRelationships>
      <bends><x>1.5</x><y>2</y></bends>
      <backPressureObjectThreshold>10000</backPressureObjectThreshold>
    </connections>
  </snippet>
</template>"""
    source = parse_template_xml(xml)
    assert source.name == "Tiny" and source.encoding_version == "1.2" and source.group_id == "g"
    processor = source.snippet["processors"][0]
    assert processor["relationships"] == [{"autoTerminate": "true", "name": "success"}]
    assert processor["config"]["properties"] == {"Log Level": "warn", "Log prefix": None}
    assert processor["style"] == {}
    connection = source.snippet["connections"][0]
    assert connection["selectedRelationships"] == ["success"]
    assert connection["bends"] == [{"x": "1.5", "y": "2"}]

    document = convert_template(source)
    versioned = document["flowContents"]["processors"][0]
    assert versioned["properties"] == {"Log Level": "warn"}  # unset values are dropped
    assert versioned["propertyDescriptors"]["Log prefix"]["sensitive"] is True
    assert versioned["autoTerminatedRelationships"] == ["success"]
    assert versioned["scheduledState"] == "DISABLED"
    assert versioned["runDurationMillis"] == 25
    assert versioned["concurrentlySchedulableTaskCount"] == 3
    assert versioned["retriedRelationships"] == []
    assert "retryCount" not in versioned  # older template: NiFi applies its default
    versioned_connection = document["flowContents"]["connections"][0]
    assert versioned_connection["bends"] == [{"x": 1.5, "y": 2.0}]
    assert versioned_connection["backPressureObjectThreshold"] == 10000
    assert versioned_connection["source"]["name"] == "Only"
    assert document["flowContents"]["identifier"] == "g"
    assert document["flowContents"]["comments"] == ""


def test_xml_reader_rejects_other_documents() -> None:
    with pytest.raises(LoadError):
        parse_template_xml(b"<flowController/>")
    with pytest.raises(LoadError):
        parse_template_xml(b"<template><name>x</name></template>")
    with pytest.raises(LoadError):
        parse_template_xml(b"not xml")


def test_empty_snippet_gets_a_stable_identifier() -> None:
    source = TemplateSource(name="Empty", description="", encoding_version="1.3", snippet={})
    first = convert_template(source)
    assert first == convert_template(source)
    assert first["flowContents"]["processors"] == []
    assert first["flowContents"]["identifier"]


def test_slugify() -> None:
    assert slugify("Removed Components Template") == "removed-components-template"
    assert slugify("  Ünïcode & symbols!  ") == "n-code-symbols"
    assert slugify("!!!") == "template"
