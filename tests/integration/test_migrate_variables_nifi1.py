"""Load the migrated fixture flow into a real NiFi 1.28.1 and compare it with the original.

Needs Docker and the ``apache/nifi:1.28.1`` image (pulled on first use). Two
containers start in parallel: one with the original ``flow.json.gz``, one
with the migrated flow. Checks:

- every processor and controller service of the migrated flow has no
  validation error that the same component did not already have;
- the parameter contexts, their parameters, inheritance and assignments
  exist as ``changes.json`` says;
- a context's own parameter wins over an inherited one of the same name
  (undocumented in the user guide, see NIFI-9891), which the migration
  relies on for shadowed variables.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from flowport.catalog import load_catalog
from flowport.loaders import load
from flowport.transforms.variables import migrate_variables
from flowport.writers import write
from tests.integration.nifi import Instance, docker, start

pytestmark = pytest.mark.integration

IMAGE = "apache/nifi:1.28.1"
FLOW = Path(__file__).resolve().parent.parent / "fixtures" / "nifi-1.28.1" / "flow.json.gz"
PORTS = {"original": 18080, "migrated": 18081}
# Directories the fixture's GetFile processors point at. NiFi skips the
# "directory exists" check while the value holds ${...}, but validates the
# literal once #{...} is substituted; with the directories present both
# instances validate the same way.
DATA_DIRECTORIES = ("data", "data/in", "data/in/from-child")


@pytest.fixture(scope="module")
def instances(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[Instance, Instance, Path]]:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    out_dir = tmp_path_factory.mktemp("migrated")
    result = migrate_variables(load(FLOW), load_catalog())
    migrated = out_dir / "flow.json.gz"
    write(result.flow, migrated)
    (out_dir / "changes.json").write_text(
        json.dumps([c.model_dump() for c in result.changes]), encoding="utf-8"
    )
    original = start(
        "flowport-it-original",
        IMAGE,
        PORTS["original"],
        flow_gz=FLOW.read_bytes(),
        directories=DATA_DIRECTORIES,
    )
    migrated_instance = start(
        "flowport-it-migrated",
        IMAGE,
        PORTS["migrated"],
        flow_gz=migrated.read_bytes(),
        directories=DATA_DIRECTORIES,
    )
    try:
        original.api.wait_ready(original.container)
        migrated_instance.api.wait_ready(migrated_instance.container)
        yield original, migrated_instance, out_dir
    finally:
        docker("rm", "-f", original.container, migrated_instance.container, check=False)


def test_migrated_flow_has_no_new_validation_errors(
    instances: tuple[Instance, Instance, Path],
) -> None:
    original, migrated, _ = instances
    before = original.api.wait_validated()
    after = migrated.api.wait_validated()
    assert set(after) == set(before), "the migrated flow must keep every component"
    new_errors = {
        c.name: sorted(c.errors - before[cid].errors)
        for cid, c in after.items()
        if c.errors - before[cid].errors
    }
    assert new_errors == {}
    # The rewritten properties resolve: no error mentions a parameter.
    for name in ("Listen on variable port", "Get from variable path", "Get from inherited path"):
        component = next(c for c in after.values() if c.name == name)
        assert not [e for e in component.errors if "arameter" in e or "#{" in e], component


def test_contexts_parameters_and_assignments_match_changes(
    instances: tuple[Instance, Instance, Path],
) -> None:
    _, migrated, out_dir = instances
    changes = json.loads((out_dir / "changes.json").read_text(encoding="utf-8"))
    entities = migrated.api.get("/flow/parameter-contexts")["parameterContexts"]
    contexts = {e["component"]["name"]: e for e in entities}

    created = {c["context"] for c in changes if c["kind"] == "create-context"}
    assert created <= set(contexts)
    for context_name in created:
        component = contexts[context_name]["component"]
        expected = sorted(
            c["property"]
            for c in changes
            if c["kind"] == "add-parameter" and c["context"] == context_name
        )
        assert sorted(p["parameter"]["name"] for p in component["parameters"]) == expected
        inherits = [
            c["new"]
            for c in changes
            if c["kind"] == "create-context" and c["context"] == context_name
        ]
        assert [i["component"]["name"] for i in component["inheritedParameterContexts"]] == [
            i for i in inherits if i
        ]

    groups = migrated.api.groups()
    for change in changes:
        if change["kind"] == "assign-context":
            assert groups[change["component_name"]]["context"] == change["context"], change
    assert groups["Existing Context"]["context"] == "Fixture Context"
    assert [
        p["parameter"]["name"] for p in contexts["Fixture Context"]["component"]["parameters"]
    ] == ["host", "timeout"]


def test_own_parameter_wins_over_inherited(instances: tuple[Instance, Instance, Path]) -> None:
    _, migrated, _ = instances
    entities = migrated.api.get("/flow/parameter-contexts")["parameterContexts"]
    context_id = next(e["id"] for e in entities if e["component"]["name"] == "Child Variables")
    entity = migrated.api.get(f"/parameter-contexts/{context_id}?includeInheritedParameters=true")
    effective = {p["parameter"]["name"]: p["parameter"] for p in entity["component"]["parameters"]}
    origin = {name: p["parameterContext"]["component"]["name"] for name, p in effective.items()}
    assert origin == {
        "shared": "Child Variables",
        "path": "Child Variables",
        "host": "Variables Variables",
        "port": "Variables Variables",
    }
    assert effective["shared"]["value"] == "from-child"
    assert effective["host"]["value"] == "example.org"
