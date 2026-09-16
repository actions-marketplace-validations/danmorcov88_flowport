"""Rule behavior on small synthetic flows."""

from typing import Any

import pytest

from flowport.catalog import Catalog, load_catalog
from flowport.loaders import load_flow_document
from flowport.rules import Finding, analyze

STD = "org.apache.nifi.processors.standard."
APACHE = {"group": "org.apache.nifi", "artifact": "nifi-standard-nar", "version": "1.28.1"}


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog()


def processor(
    name: str,
    type_name: str,
    properties: dict[str, str | None] | None = None,
    *,
    bundle: dict[str, str] | None = None,
    scheduling: str = "TIMER_DRIVEN",
    period: str = "0 sec",
) -> dict[str, Any]:
    return {
        "identifier": f"id-{name}",
        "instanceIdentifier": f"inst-{name}",
        "name": name,
        "type": type_name,
        "bundle": bundle or APACHE,
        "properties": properties or {},
        "schedulingStrategy": scheduling,
        "schedulingPeriod": period,
        "componentType": "PROCESSOR",
    }


def group(
    name: str,
    processors: list[dict[str, Any]] | None = None,
    *,
    variables: dict[str, str] | None = None,
    groups: list[dict[str, Any]] | None = None,
    services: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "identifier": f"id-{name}",
        "instanceIdentifier": f"inst-{name}",
        "name": name,
        "processors": processors or [],
        "controllerServices": services or [],
        "processGroups": groups or [],
        "variables": variables or {},
        "componentType": "PROCESS_GROUP",
    }


def run(catalog: Catalog, root: dict[str, Any], **extra: Any) -> list[Finding]:
    document: dict[str, Any] = {"encodingVersion": {"majorVersion": 2, "minorVersion": 0}}
    document.update(extra)
    document["rootGroup"] = root
    return analyze(load_flow_document(document), catalog)


def rule_ids(findings: list[Finding]) -> list[str]:
    return [f.rule_id for f in findings]


def test_supported_component_has_no_findings(catalog: Catalog) -> None:
    root = group("root", [processor("a", STD + "UpdateAttribute".replace("standard.", ""))])
    root["processors"][0]["type"] = "org.apache.nifi.processors.attributes.UpdateAttribute"
    assert run(catalog, root) == []


def test_removed_component_cites_jira_and_alternative(catalog: Catalog) -> None:
    findings = run(catalog, group("root", [processor("get", STD + "GetHTTP")]))
    assert rule_ids(findings) == ["NIFI2-REMOVED-COMPONENT"]
    finding = findings[0]
    assert finding.severity == "BLOCKER"
    assert "InvokeHTTP" in finding.suggestion
    assert "https://issues.apache.org/jira/browse/NIFI-11174" in finding.sources
    assert finding.location.path == "/root"
    assert finding.location.id == "inst-get"


def test_renamed_component_points_at_new_type(catalog: Catalog) -> None:
    findings = run(catalog, group("root", [processor("jolt", STD + "JoltTransformJSON")]))
    assert rule_ids(findings) == ["NIFI2-RENAMED-COMPONENT"]
    assert findings[0].details["to_type"] == "org.apache.nifi.processors.jolt.JoltTransformJSON"
    assert "nifi-jolt-nar" in findings[0].details["to_bundle"]


def test_third_party_bundle(catalog: Catalog) -> None:
    custom = {"group": "com.example", "artifact": "x-nar", "version": "1.0"}
    findings = run(catalog, group("root", [processor("c", "com.example.Custom", bundle=custom)]))
    assert rule_ids(findings) == ["NIFI2-THIRD-PARTY-BUNDLE"]
    assert findings[0].severity == "MANUAL"


def test_unknown_apache_type(catalog: Catalog) -> None:
    findings = run(catalog, group("root", [processor("u", "org.apache.nifi.processors.Future")]))
    assert rule_ids(findings) == ["NIFI2-UNKNOWN-COMPONENT"]


def test_optional_bundle(catalog: Catalog) -> None:
    hadoop = {"group": "org.apache.nifi", "artifact": "nifi-hadoop-nar", "version": "1.28.1"}
    findings = run(
        catalog,
        group("root", [processor("h", "org.apache.nifi.processors.hadoop.PutHDFS", bundle=hadoop)]),
    )
    assert rule_ids(findings) == ["NIFI2-OPTIONAL-BUNDLE"]
    assert findings[0].details["profile"] == "include-hadoop"


def test_deprecated_in_target(catalog: Catalog) -> None:
    findings = run(catalog, group("root", [processor("c", STD + "CompressContent")]))
    assert rule_ids(findings) == ["NIFI2-DEPRECATED-IN-TARGET"]
    assert findings[0].severity == "INFO"


@pytest.mark.parametrize(
    ("type_name", "engine", "expected"),
    [
        ("org.apache.nifi.processors.script.ExecuteScript", "python", ["NIFI2-SCRIPT-ENGINE"]),
        ("org.apache.nifi.processors.script.ExecuteScript", "Clojure", []),
        ("org.apache.nifi.processors.script.ExecuteScript", "Groovy", []),
        (
            "org.apache.nifi.processors.script.InvokeScriptedProcessor",
            "Clojure",
            ["NIFI2-SCRIPT-ENGINE"],
        ),
    ],
)
def test_script_engines(catalog: Catalog, type_name: str, engine: str, expected: list[str]) -> None:
    scripting = {"group": "org.apache.nifi", "artifact": "nifi-scripting-nar", "version": "1.28.1"}
    findings = run(
        catalog,
        group("root", [processor("s", type_name, {"Script Engine": engine}, bundle=scripting)]),
    )
    assert rule_ids(findings) == expected


def test_event_driven_and_cron(catalog: Catalog) -> None:
    root = group(
        "root",
        [
            processor("e", STD + "GenerateFlowFile", scheduling="EVENT_DRIVEN"),
            processor(
                "y", STD + "GenerateFlowFile", scheduling="CRON_DRIVEN", period="0 0 12 * * ? 2030"
            ),
            processor(
                "d", STD + "GenerateFlowFile", scheduling="CRON_DRIVEN", period="0 0 6 ? * 1-5"
            ),
            processor(
                "ok", STD + "GenerateFlowFile", scheduling="CRON_DRIVEN", period="0 0 6 ? * MON-FRI"
            ),
        ],
    )
    findings = run(catalog, root)
    assert sorted(rule_ids(findings)) == [
        "NIFI2-CRON-NUMERIC-DAY-OF-WEEK",
        "NIFI2-CRON-YEAR-FIELD",
        "NIFI2-EVENT-DRIVEN",
    ]


def test_variable_reference_classification(catalog: Catalog) -> None:
    root = group(
        "root",
        [
            processor("registry", STD + "GetFile", {"Input Directory": "${dir}"}),
            processor("attrs", STD + "PutFile", {"Directory": "${dir}"}),
            processor("func", STD + "GetFile", {"Input Directory": "${dir:toLower()}"}),
            processor("noel", STD + "LogAttribute", {"Attributes to Log": "${dir}"}),
            processor("unknown", "org.apache.nifi.processors.Future", {"x": "${dir}"}),
            processor("notvar", STD + "GetFile", {"Input Directory": "${other}"}),
        ],
        variables={"dir": "/data", "unused": "1"},
    )
    by_name = {f.location.name: f.rule_id for f in run(catalog, root) if "variable" in f.details}
    assert by_name == {
        "registry": "NIFI2-VARIABLE-REFERENCE",
        "attrs": "NIFI2-VARIABLE-REFERENCE-ATTRIBUTE-SCOPE",
        "func": "NIFI2-VARIABLE-REFERENCE-FUNCTION",
        "noel": "NIFI2-VARIABLE-REFERENCE-NO-EL",
        "unknown": "NIFI2-VARIABLE-REFERENCE-UNKNOWN-SCOPE",
        "root": "NIFI2-VARIABLE-UNUSED",
    }
    unused = [f for f in run(catalog, root) if f.rule_id == "NIFI2-VARIABLE-UNUSED"]
    assert [f.details["variable"] for f in unused] == ["unused"]


def test_variable_inheritance_and_shadowing(catalog: Catalog) -> None:
    grandchild = group(
        "grandchild", [processor("g", STD + "GetFile", {"Input Directory": "${a}/${b}"})]
    )
    child = group("child", groups=[grandchild], variables={"b": "child"})
    root = group("root", groups=[child], variables={"a": "1", "b": "root"})
    findings = run(catalog, root)
    refs = {
        f.details["variable"]: f.details["defined_in"]
        for f in findings
        if f.rule_id == "NIFI2-VARIABLE-REFERENCE"
    }
    assert refs == {"a": "/root", "b": "/root/child"}
    unused = {
        (f.location.path, f.details["variable"])
        for f in findings
        if f.rule_id == "NIFI2-VARIABLE-UNUSED"
    }
    assert unused == {("/root", "b")}


def test_variable_name_rule(catalog: Catalog) -> None:
    findings = run(catalog, group("root", variables={"ok-name_1.x y": "v", "bad/name": "v"}))
    names = [f.details["variable"] for f in findings if f.rule_id == "NIFI2-VARIABLE-NAME"]
    assert names == ["bad/name"]


def test_templates_at_flow_level(catalog: Catalog) -> None:
    root = group("root")
    template = {
        "identifier": "t1",
        "name": "T",
        "groupIdentifier": "inst-root",
        "componentType": "TEMPLATE",
    }
    findings = run(catalog, root, templates=[template])
    assert rule_ids(findings) == ["NIFI2-TEMPLATE"]
    assert findings[0].location.path == "/root"
    assert findings[0].location.kind == "TEMPLATE"


def test_invokehttp_proxy_needs_a_host(catalog: Catalog) -> None:
    with_host = processor("p", STD + "InvokeHTTP", {"Proxy Host": "proxy", "Proxy Type": "http"})
    default_only = processor("q", STD + "InvokeHTTP", {"Proxy Type": "http"})
    findings = run(catalog, group("root", [with_host, default_only]))
    assert [(f.location.name, f.rule_id) for f in findings] == [
        ("p", "NIFI2-INVOKEHTTP-PROXY-PROPERTIES")
    ]


def test_findings_are_sorted_deterministically(catalog: Catalog) -> None:
    root = group("root", [processor("b", STD + "GetHTTP"), processor("a", STD + "PostHTTP")])
    first = [f.location.name for f in run(catalog, root)]
    root["processors"].reverse()
    second = [f.location.name for f in run(catalog, root)]
    assert first == second == ["a", "b"]
