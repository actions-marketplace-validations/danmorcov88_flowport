"""Import migrated fixtures into a real NiFi 2.12.0 through the REST API.

Needs Docker and the ``apache/nifi:2.12.0`` image. Everything goes through
the endpoint behind "Upload flow definition".

Templates: each template of the fixture flow is converted and uploaded; the
group must hold as many processors, controller services, connections, ports,
funnels, labels, remote groups and nested groups as the definition, and
components must be valid or invalid only for reasons the definition cannot
fix (disabled controller services). Removed types load as ghosts, which is
what the report says.

Components: the replacements fixture is migrated and uploaded; every
replaced component must exist under its new type, be valid (or reference
its services correctly), and its connections must carry the remapped
relationships. The event-driven processor of the scheduling fixture must
import as timer-driven and valid.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from flowport.catalog import load_catalog
from flowport.loaders import load
from flowport.loaders.templates import templates_in_flow
from flowport.nifi import NiFiClient
from flowport.nifi.docker import start_container
from flowport.transforms.templates import ConvertedTemplate, convert_and_analyze

pytestmark = pytest.mark.integration

IMAGE = "apache/nifi:2.12.0"
FLOW = Path(__file__).resolve().parent.parent / "fixtures" / "nifi-1.28.1" / "flow.json.gz"
PORT = 18443


@pytest.fixture(scope="module")
def nifi2() -> Iterator[NiFiClient]:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    container = start_container("flowport-it-nifi2", IMAGE, PORT, https=True)
    try:
        yield container.wait_ready()
    finally:
        container.remove()


@pytest.fixture(scope="module")
def converted() -> dict[str, ConvertedTemplate]:
    catalog = load_catalog()
    flow = load(FLOW)
    return {t.name: convert_and_analyze(t, catalog) for t in templates_in_flow(flow.raw)}


def test_clean_template_imports_with_every_component(
    nifi2: NiFiClient, converted: dict[str, ConvertedTemplate]
) -> None:
    template = converted["Template Conversion Template"]
    assert template.findings == []
    group_id = nifi2.upload_definition("root", "Converted", template.document)
    assert nifi2.count_components(group_id) == template.counts()

    components = nifi2.wait_validated(group_id)
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

    flow = nifi2.group_flow(group_id)
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
    nifi2: NiFiClient, converted: dict[str, ConvertedTemplate]
) -> None:
    template = converted["Removed Components Template"]
    flagged = {f.location.name for f in template.findings if f.rule_id == "NIFI2-REMOVED-COMPONENT"}
    assert flagged == {"Fetch checksum", "Post to listener", "Hash content", "Hash attribute"}
    group_id = nifi2.upload_definition("root", "Ghosts", template.document)
    assert nifi2.count_components(group_id) == template.counts()
    flow = nifi2.group_flow(group_id)
    ghosts = {
        p["component"]["name"] for p in flow["processors"] if p["component"].get("extensionMissing")
    }
    assert ghosts == flagged


# -- migrate components -------------------------------------------------------


def test_replaced_components_import_and_validate(nifi2: NiFiClient) -> None:
    from flowport.transforms.components import migrate_components

    catalog = load_catalog()
    definitions = FLOW.parent / "definitions"
    result = migrate_components(load(definitions / "replacements.json"), catalog)
    skipped = [f for f in result.findings if f.rule_id == "NIFI2-REPLACEMENT-SKIPPED"]
    assert [f.location.name for f in skipped] == ["Post packaged"]

    group_id = nifi2.upload_definition("root", "Replacements", result.flow.raw)
    components = nifi2.wait_validated(group_id)
    by_name = {c.name: c for c in components.values()}
    flow = nifi2.group_flow(group_id)
    ghosts = {
        p["component"]["name"] for p in flow["processors"] if p["component"].get("extensionMissing")
    }
    assert ghosts == {"Post packaged"}, "only the guarded PostHTTP stays a ghost"

    types = {p["component"]["name"]: p["component"]["type"] for p in flow["processors"]}
    assert types["Fetch"] == types["Post"] == "org.apache.nifi.processors.standard.InvokeHTTP"
    assert types["Encode"] == "org.apache.nifi.processors.standard.EncodeContent"
    assert types["Jolt"] == "org.apache.nifi.processors.jolt.JoltTransformJSON"
    assert types["Jolt record"] == "org.apache.nifi.processors.jolt.JoltTransformRecord"
    for name in ("Fetch", "Post", "Encode", "Jolt", "Sent", "Failed"):
        assert by_name[name].status == "VALID", (name, sorted(by_name[name].errors))
    # Processors referencing the renamed cache client / record services: the
    # references survived, the only complaint is that the services are disabled.
    for name in ("Dedupe", "Jolt record"):
        property_errors = [e for e in by_name[name].errors if "validated against" in e]
        assert property_errors, name
        assert all("is disabled" in e for e in property_errors), (name, property_errors)
    services = {
        c.name: c for c in components.values() if c.type.endswith("Service") or "Cache" in c.type
    }
    assert (
        services["Cache client"].type
        == "org.apache.nifi.distributed.cache.client.MapCacheClientService"
    )
    assert (
        services["Cache server"].type
        == "org.apache.nifi.distributed.cache.server.map.MapCacheServer"
    )
    assert (
        services["Set client"].type
        == "org.apache.nifi.distributed.cache.client.SetCacheClientService"
    )
    assert services["Set server"].type == "org.apache.nifi.distributed.cache.server.SetCacheServer"
    for name in ("Cache client", "Cache server", "Set client", "Set server"):
        assert services[name].status == "VALID", (name, sorted(services[name].errors))

    fetch = next(p["component"] for p in flow["processors"] if p["component"]["name"] == "Fetch")
    props = fetch["config"]["properties"]
    assert props["HTTP Method"] == "GET"
    assert props["HTTP URL"] == "https://example.org/data.json"
    assert props["Response FlowFile Naming Strategy"] == "URL_PATH"
    assert props["Response Redirects Enabled"] == "True"
    assert props["Request User-Agent"] == "flowport-fixture"
    post = next(p["component"] for p in flow["processors"] if p["component"]["name"] == "Post")
    assert post["config"]["properties"]["Request Content-Encoding"] == "GZIP"
    assert post["config"]["properties"]["Request Header Attributes Pattern"] == "x-.*"
    assert set(post["config"]["autoTerminatedRelationships"]) == {"Response"}

    connections = {
        (c["component"]["source"]["name"], c["component"]["destination"]["name"]): set(
            c["component"]["selectedRelationships"]
        )
        for c in flow["connections"]
    }
    assert connections[("Fetch", "Encode")] == {"Response"}
    assert connections[("Post", "Sent")] == {"Original"}
    assert connections[("Post", "Failed")] == {"Failure", "Retry", "No Retry"}
    assert connections[("Encode", "Dedupe")] == {"success"}


def test_event_driven_processor_imports_as_timer_driven(nifi2: NiFiClient) -> None:
    from flowport.transforms.components import migrate_components

    result = migrate_components(
        load(FLOW.parent / "definitions" / "scheduling.json"), load_catalog()
    )
    group_id = nifi2.upload_definition("root", "Scheduling", result.flow.raw)
    flow = nifi2.group_flow(group_id)
    event = next(
        p["component"] for p in flow["processors"] if p["component"]["name"] == "Event driven"
    )
    assert event["config"]["schedulingStrategy"] == "TIMER_DRIVEN"
    assert event["config"]["schedulingPeriod"] == "0 sec"
    components = nifi2.wait_validated(group_id)
    assert next(c for c in components.values() if c.name == "Event driven").status == "VALID"
