"""Start NiFi 2.12.0 with the fully migrated fixture flow as ``conf/flow.json.gz``.

This is the path an upgrade takes: the ``flow.json.gz`` written by
``flowport migrate all`` goes into ``conf/`` of a NiFi 2.x and NiFi loads it
at startup. The original fixture cannot start 2.x at all (event-driven
scheduling, see ``docs/dev/nifi2-load-experiment.md``). Checks:

- NiFi starts and the API answers;
- every component that is invalid or missing on 2.x is one the analyzer
  reports as BLOCKER on the migrated flow, or is invalid for a reason the
  fixture causes on purpose (unconnected relationships, disabled services,
  no upstream connection, directories that do not exist in the container);
- the parameter contexts created for the variables exist and are assigned;
- ``flowport validate`` against the same instance agrees with what the API
  says and cleans up after itself.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from flowport.catalog import load_catalog
from flowport.loaders import load
from flowport.nifi import ComponentState, NiFiClient
from flowport.nifi.docker import start_container
from flowport.rules import analyze
from flowport.transforms.pipeline import migrate_all
from flowport.validation import validate_flow
from flowport.writers import write

pytestmark = pytest.mark.integration

IMAGE = "apache/nifi:2.12.0"
FLOW = Path(__file__).resolve().parent.parent / "fixtures" / "nifi-1.28.1" / "flow.json.gz"
PORT = 18444
# Validation errors the fixture causes on purpose, not the migration.
FIXTURE_NOISE = re.compile(
    r"is not connected to any component and is not auto-terminated"
    r"|Controller Service with ID .* is disabled"
    r"|requires an upstream connection"
    r"|Directory does not exist"
    r"|is invalid because Directory .* does not exist"
    r"|'Record (Reader|Writer)' is invalid because Record (Reader|Writer) is required"
)


@pytest.fixture(scope="module")
def migrated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("all") / "flow.json.gz"
    result = migrate_all(load(FLOW), load_catalog())
    write(result.flow_result.flow, out)
    return out


@pytest.fixture(scope="module")
def nifi2(migrated: Path) -> Iterator[NiFiClient]:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    container = start_container(
        "flowport-it-nifi2-startup",
        IMAGE,
        PORT,
        https=True,
        flow_gz=migrated.read_bytes(),
        sensitive_props_key="flowport-fixtures-key",
    )
    try:
        yield container.wait_ready()
    finally:
        container.remove()


def _explained(component: ComponentState, blockers: set[str]) -> bool:
    if component.id in blockers:
        return True
    return all(FIXTURE_NOISE.search(error) for error in component.errors)


def test_migrated_flow_starts_and_every_problem_is_reported(
    nifi2: NiFiClient, migrated: Path
) -> None:
    flow = load(migrated)
    findings = analyze(flow, load_catalog())
    # Ghosts of third-party and optional bundles are MANUAL: the fix is a NAR, not the flow.
    blockers = {
        f.location.id
        for f in findings
        if f.severity.value == "BLOCKER"
        or f.rule_id in {"NIFI2-THIRD-PARTY-BUNDLE", "NIFI2-OPTIONAL-BUNDLE"}
    }

    components = nifi2.wait_validated()
    assert len(components) > 50
    unexplained = {
        f"{c.path} > {c.name}": (c.type, c.errors)
        for c in components.values()
        if not c.valid and not _explained(c, blockers)
    }
    assert unexplained == {}
    ghosts = {c.id for c in components.values() if c.ghost}
    assert ghosts <= blockers, "every missing type must be a BLOCKER finding"
    # And the replaced components are not among the ghosts.
    by_name = {c.name: c for c in components.values()}
    for name in ("Fetch", "Post", "Encode", "Jolt", "Base64 encode", "Fetch checksum"):
        assert not by_name[name].ghost, name
        assert by_name[name].status == "VALID" or _explained(by_name[name], set()), by_name[name]
    assert by_name["Event driven"].status == "VALID"


def test_parameter_contexts_survive_the_upgrade(nifi2: NiFiClient) -> None:
    contexts = nifi2.parameter_contexts()
    assert {"Variables Variables", "Child Variables"} <= set(contexts)
    groups = nifi2.groups()
    assert groups["Variables"]["context"] == "Variables Variables"
    assert groups["Grandchild"]["context"] == "Child Variables"


def test_validate_agrees_with_the_api_and_cleans_up(nifi2: NiFiClient, migrated: Path) -> None:
    before = set(nifi2.groups())
    result = validate_flow(load(migrated), nifi2, group_name="flowport validation test")
    assert result.nifi_version.startswith("2.12")
    assert result.summary()["components"] > 50
    assert result.summary()["ghosts"] >= 10
    assert any("template" in w for w in result.warnings)
    assert any("reporting tasks" in w for w in result.warnings)
    assert set(nifi2.groups()) == before, f"the validation group must be deleted: {result.warnings}"
    names = {c.name for c in result.invalid}
    assert "Post packaged" in names and "Event driven" not in names
