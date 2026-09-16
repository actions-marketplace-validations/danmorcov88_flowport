import gzip
import json
from pathlib import Path

import pytest

from flowport.loaders import LoadError, UnsupportedInputError, detect_kind, load
from flowport.model import ComponentKind, InputKind


def test_detect_kind_by_content() -> None:
    assert detect_kind(b'{"rootGroup": {}}') == InputKind.FLOW
    assert detect_kind(b'  {"flowContents": {}}') == InputKind.DEFINITION
    assert (
        detect_kind(b'<?xml version="1.0"?><template encoding-version="1.3">') == InputKind.TEMPLATE
    )
    assert detect_kind(b"<flowController>") == InputKind.FLOW_XML


def test_detect_kind_rejects_garbage() -> None:
    with pytest.raises(LoadError):
        detect_kind(b"hello")
    with pytest.raises(LoadError):
        detect_kind(b'{"something": 1}')
    with pytest.raises(LoadError):
        detect_kind(b"{not json")


def test_load_flow_gz_and_flow_json_give_same_model(flow_gz: Path, flow_json: Path) -> None:
    a = load(flow_gz)
    b = load(flow_json)
    assert a.kind == b.kind == InputKind.FLOW
    assert a.raw == b.raw
    assert [g.path for g in a.groups()] == [g.path for g in b.groups()]


def test_flow_model_structure(flow_gz: Path) -> None:
    flow = load(flow_gz)
    paths = [g.path for g in flow.groups()]
    assert paths[0] == "/NiFi Flow"
    assert "/NiFi Flow/Variables/Child/Grandchild" in paths
    variables = next(g for g in flow.groups() if g.path == "/NiFi Flow/Variables")
    assert variables.variables["host"] == "example.org"
    assert len(flow.templates) == 1
    assert [t.name for t in flow.reporting_tasks] == ["Prometheus metrics"]
    assert {c.name for c in flow.parameter_contexts} == {
        "Base Context",
        "Fixture Context",
        "Clean Context",
    }
    ctx = next(c for c in flow.parameter_contexts if c.name == "Fixture Context")
    assert ctx.inherited == ["Base Context"]
    assert flow.bundle_versions == {
        "1.28.1": sum(1 for c in flow.components() if c.bundle.group == "org.apache.nifi")
    }


def test_component_fields(flow_gz: Path) -> None:
    flow = load(flow_gz)
    listen = next(c for c in flow.components() if c.name == "Listen on variable port")
    assert listen.kind == ComponentKind.PROCESSOR
    assert listen.type == "org.apache.nifi.processors.standard.ListenHTTP"
    assert listen.short_type == "ListenHTTP"
    assert listen.bundle.coordinate == "org.apache.nifi:nifi-standard-nar:1.28.1"
    assert listen.properties["Listening Port"] == "${port}"
    assert listen.path == "/NiFi Flow/Variables"
    assert listen.id == listen.raw["instanceIdentifier"]
    assert listen.raw is not None
    custom = next(c for c in flow.components() if c.name == "Custom Processor")
    assert custom.bundle.group == "com.example"


def test_load_definition(definitions_dir: Path) -> None:
    flow = load(definitions_dir / "existing-context.json")
    assert flow.kind == InputKind.DEFINITION
    assert flow.root.path == "/Existing Context"
    assert flow.root.parameter_context_name == "Fixture Context"
    assert flow.root.variables == {"retries": "3", "host": "variable.example.org"}
    names = {c.name for c in flow.parameter_contexts}
    assert names == {"Base Context", "Fixture Context"}


def test_unknown_fields_are_kept_and_ignored(tmp_path: Path, flow_json: Path) -> None:
    document = json.loads(flow_json.read_text(encoding="utf-8"))
    document["futureField"] = {"nested": [1, 2, 3]}
    document["rootGroup"]["processGroups"][0]["processors"][0]["newProcessorField"] = "x"
    path = tmp_path / "flow.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    flow = load(path)
    assert flow.raw["futureField"] == {"nested": [1, 2, 3]}
    first = flow.root.groups[0].processors[0]
    assert first.raw["newProcessorField"] == "x"


def test_flow_xml_is_rejected_with_guidance(tmp_path: Path) -> None:
    path = tmp_path / "flow.xml.gz"
    path.write_bytes(
        gzip.compress(
            b'<?xml version="1.0"?><flowController encoding-version="1.4"></flowController>'
        )
    )
    with pytest.raises(UnsupportedInputError, match=r"Migration\+Guidance"):
        load(path)


def test_template_xml_is_rejected_for_now(fixtures_dir: Path) -> None:
    with pytest.raises(UnsupportedInputError, match="template"):
        load(fixtures_dir / "nifi-1.28.1" / "templates" / "removed-components.xml")


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(LoadError):
        load(tmp_path / "nope.json")
