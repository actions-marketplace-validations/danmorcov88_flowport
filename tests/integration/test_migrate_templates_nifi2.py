"""Import the converted fixture templates into a real NiFi 2.12.0 through the REST API.

Needs Docker and the ``apache/nifi:2.12.0`` image. Each template of the
fixture flow is converted to a flow definition and uploaded with the same
endpoint the UI's "Upload flow definition" uses. Checks:

- the upload succeeds and creates a process group;
- the group holds as many processors, controller services, connections,
  ports, funnels, labels, remote groups and nested groups as the definition;
- components that exist on 2.x are valid or invalid only for reasons the
  definition cannot fix (disabled controller services); components the
  analyzer flags as removed load as ghosts, which is what the report says.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from flowport.catalog import load_catalog
from flowport.loaders import load
from flowport.loaders.templates import templates_in_flow
from flowport.transforms.templates import ConvertedTemplate, convert_and_analyze
from flowport.writers import dumps
from tests.integration.nifi import Instance, docker, start

pytestmark = pytest.mark.integration

IMAGE = "apache/nifi:2.12.0"
FLOW = Path(__file__).resolve().parent.parent / "fixtures" / "nifi-1.28.1" / "flow.json.gz"
PORT = 18443


@pytest.fixture(scope="module")
def nifi2() -> Iterator[Instance]:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    instance = start("flowport-it-nifi2", IMAGE, PORT, https=True)
    try:
        instance.api.wait_ready(instance.container)
        yield instance
    finally:
        docker("rm", "-f", instance.container, check=False)


@pytest.fixture(scope="module")
def converted() -> dict[str, ConvertedTemplate]:
    catalog = load_catalog()
    flow = load(FLOW)
    return {t.name: convert_and_analyze(t, catalog) for t in templates_in_flow(flow.raw)}


def test_clean_template_imports_with_every_component(
    nifi2: Instance, converted: dict[str, ConvertedTemplate]
) -> None:
    template = converted["Template Conversion Template"]
    assert template.findings == []
    group_id = nifi2.api.upload_definition("root", "Converted", dumps(template.document).encode())
    assert nifi2.api.count_components(group_id) == template.counts()

    components = nifi2.api.wait_validated(group_id)
    by_name = {c.name: c for c in components.values()}
    assert set(by_name) == {"Generate", "Convert", "Done", "Log", "Reader", "Writer"}
    for name in ("Generate", "Done", "Log", "Reader", "Writer"):
        assert by_name[name].status == "VALID", (name, sorted(by_name[name].errors))
    # ConvertRecord references the (disabled) services by id: the references
    # were remapped on import, so the only complaint is that they are disabled.
    convert = by_name["Convert"]
    assert convert.errors, "expected the disabled-service validation error"
    assert all("disabled" in e.lower() or "not enabled" in e.lower() for e in convert.errors), (
        sorted(convert.errors)
    )

    flow = nifi2.api.group_flow(group_id)
    connections = {
        (c["component"]["source"]["name"], c["component"]["destination"]["name"])
        for c in flow["connections"]
    }
    assert connections == {
        ("Generate", "Convert"),
        ("Convert", "Funnel"),
        ("Funnel", "in"),
        ("out", "Done"),
    }
    named = next(c["component"] for c in flow["connections"] if c["component"].get("name"))
    assert named["name"] == "generated"
    assert named["backPressureObjectThreshold"] == 500
    assert named["prioritizers"] == ["org.apache.nifi.prioritizer.FirstInFirstOutPrioritizer"]
    assert named["bends"] == [{"x": 300.0, "y": 100.0}]
    remote = flow["remoteProcessGroups"][0]["component"]
    assert remote["targetUris"] == "http://localhost:8080/nifi"
    assert [p["name"] for p in remote["contents"]["inputPorts"]] == ["Remote In"]
    assert flow["labels"][0]["component"]["label"] == "Converted from a template by flowport"


def test_template_with_removed_components_imports_as_ghosts(
    nifi2: Instance, converted: dict[str, ConvertedTemplate]
) -> None:
    template = converted["Removed Components Template"]
    flagged = {f.location.name for f in template.findings if f.rule_id == "NIFI2-REMOVED-COMPONENT"}
    assert flagged == {"Fetch checksum", "Post to listener", "Hash content", "Hash attribute"}
    group_id = nifi2.api.upload_definition("root", "Ghosts", dumps(template.document).encode())
    assert nifi2.api.count_components(group_id) == template.counts()
    flow = nifi2.api.group_flow(group_id)
    ghosts = {
        p["component"]["name"] for p in flow["processors"] if p["component"].get("extensionMissing")
    }
    assert ghosts == flagged
