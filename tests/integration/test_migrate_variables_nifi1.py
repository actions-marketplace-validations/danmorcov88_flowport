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

import io
import json
import shutil
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from flowport.catalog import load_catalog
from flowport.loaders import load
from flowport.transforms.variables import migrate_variables
from flowport.writers import write

pytestmark = pytest.mark.integration

IMAGE = "apache/nifi:1.28.1"
FLOW = Path(__file__).resolve().parent.parent / "fixtures" / "nifi-1.28.1" / "flow.json.gz"
CONF_DIR = "/opt/nifi/nifi-current/conf"
SENSITIVE_PROPS_KEY = "flowport-fixtures-key"
STARTUP_TIMEOUT = 600
# NiFi 1.x checks the Host header against its own port, so the host port and
# the container port are the same. High ports avoid clashes with local services.
PORTS = {"original": 18080, "migrated": 18081}
DATA_DIRECTORIES = ("data", "data/in", "data/in/from-child")


def docker(*args: str, stdin: bytes | None = None, check: bool = True) -> str:
    completed = subprocess.run(
        ["docker", *args], input=stdin, capture_output=True, check=False, text=stdin is None
    )
    if check and completed.returncode != 0:
        err = completed.stderr if isinstance(completed.stderr, str) else completed.stderr.decode()
        raise RuntimeError(f"docker {' '.join(args)} failed: {err}")
    out = completed.stdout
    return out if isinstance(out, str) else out.decode()


def owned_tar(name: str, data: bytes, directories: tuple[str, ...] = ()) -> bytes:
    """A tar with one file (and optional directories) owned by uid/gid 1000, NiFi's user."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for directory in directories:
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            info.uid = info.gid = 1000
            info.mode = 0o755
            info.mtime = int(time.time())
            tar.addfile(info)
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.uid = info.gid = 1000
        info.mode = 0o644
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class NiFi:
    def __init__(self, port: int) -> None:
        self.base = f"http://localhost:{port}/nifi-api"

    def get(self, path: str) -> Any:
        req = urllib.request.Request(self.base + path, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    def wait_ready(self, container: str) -> None:
        deadline = time.monotonic() + STARTUP_TIMEOUT
        last = ""
        while time.monotonic() < deadline:
            try:
                self.get("/flow/process-groups/root")
                return
            except (urllib.error.URLError, ConnectionError, OSError) as exc:
                last = str(exc)
            if "Exited" in docker(
                "ps", "-a", "--filter", f"name={container}", "--format", "{{.Status}}"
            ):
                raise RuntimeError(
                    f"{container} exited:\n{docker('logs', '--tail', '50', container)}"
                )
            time.sleep(5)
        raise RuntimeError(f"NiFi in {container} not ready after {STARTUP_TIMEOUT}s: {last}")

    def components(self) -> dict[str, Component]:
        """Every processor and controller service on the canvas by id, with validation state."""
        found: dict[str, Component] = {}

        def visit(group_id: str) -> None:
            flow = self.get(f"/flow/process-groups/{group_id}")["processGroupFlow"]["flow"]
            for entity in flow["processors"]:
                found[entity["id"]] = Component.from_entity(entity)
            services = self.get(f"/flow/process-groups/{group_id}/controller-services")
            for entity in services["controllerServices"]:
                found.setdefault(entity["id"], Component.from_entity(entity))
            for child in flow["processGroups"]:
                visit(child["id"])

        visit("root")
        return found

    def wait_validated(self) -> dict[str, Component]:
        deadline = time.monotonic() + 120
        while True:
            components = self.components()
            if not any(c.status == "VALIDATING" for c in components.values()):
                return components
            if time.monotonic() > deadline:
                raise RuntimeError("components still validating")
            time.sleep(3)

    def groups(self) -> dict[str, dict[str, Any]]:
        """Process groups by name with their assigned parameter context name."""
        found: dict[str, dict[str, Any]] = {}

        def visit(group_id: str) -> None:
            flow = self.get(f"/flow/process-groups/{group_id}")["processGroupFlow"]["flow"]
            for child in flow["processGroups"]:
                component = child["component"]
                reference = component.get("parameterContext") or {}
                found[component["name"]] = {
                    "id": child["id"],
                    "context": ((reference.get("component") or {}).get("name")),
                }
                visit(child["id"])

        visit("root")
        return found

    def parameter_contexts(self) -> dict[str, dict[str, Any]]:
        entities = self.get("/flow/parameter-contexts")["parameterContexts"]
        return {e["component"]["name"]: e for e in entities}

    def effective_parameters(self, context_id: str) -> dict[str, dict[str, Any]]:
        entity = self.get(f"/parameter-contexts/{context_id}?includeInheritedParameters=true")
        return {p["parameter"]["name"]: p["parameter"] for p in entity["component"]["parameters"]}


@dataclass(frozen=True)
class Component:
    name: str
    status: str
    errors: frozenset[str]

    @classmethod
    def from_entity(cls, entity: dict[str, Any]) -> Component:
        component = entity["component"]
        return cls(
            name=component["name"],
            status=str(component.get("validationStatus", "")),
            errors=frozenset(component.get("validationErrors") or []),
        )


@dataclass
class Instance:
    container: str
    api: NiFi


def start(container: str, port: int, flow_gz: bytes) -> Instance:
    docker("rm", "-f", container, check=False)
    docker(
        "create",
        "--name",
        container,
        "-p",
        f"{port}:{port}",
        "-e",
        f"NIFI_WEB_HTTP_PORT={port}",
        "-e",
        "NIFI_WEB_HTTP_HOST=0.0.0.0",
        "-e",
        f"NIFI_SENSITIVE_PROPS_KEY={SENSITIVE_PROPS_KEY}",
        IMAGE,
    )
    docker("cp", "-", f"{container}:{CONF_DIR}", stdin=owned_tar("flow.json.gz", flow_gz))
    # Directories the fixture's GetFile processors point at. NiFi skips the
    # "directory exists" check while the value holds ${...}, but validates the
    # literal once #{...} is substituted; with the directories present both
    # instances validate the same way.
    docker("cp", "-", f"{container}:/", stdin=owned_tar("data/.keep", b"", DATA_DIRECTORIES))
    docker("start", container)
    return Instance(container, NiFi(port))


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
    original = start("flowport-it-original", PORTS["original"], FLOW.read_bytes())
    migrated_instance = start("flowport-it-migrated", PORTS["migrated"], migrated.read_bytes())
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
    contexts = migrated.api.parameter_contexts()

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
    ] == [
        "host",
        "timeout",
    ]


def test_own_parameter_wins_over_inherited(instances: tuple[Instance, Instance, Path]) -> None:
    _, migrated, _ = instances
    contexts = migrated.api.parameter_contexts()
    effective = migrated.api.effective_parameters(contexts["Child Variables"]["id"])
    origin = {name: p["parameterContext"]["component"]["name"] for name, p in effective.items()}
    assert origin == {
        "shared": "Child Variables",
        "path": "Child Variables",
        "host": "Variables Variables",
        "port": "Variables Variables",
    }
    assert effective["shared"]["value"] == "from-child"
    assert effective["host"]["value"] == "example.org"
