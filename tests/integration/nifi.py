"""Helpers for the integration tests: NiFi 1.x and 2.x containers and a REST client.

NiFi 1.x runs plain HTTP; 2.x is HTTPS only with single-user credentials and
a bearer token. Both check the ``Host`` header against their own port, so the
host port always equals the container port; high ports avoid clashes with
local services. ``flow.json.gz`` is copied in from a tar whose entries are
owned by uid 1000 (plain ``docker cp`` writes root-owned files that NiFi
refuses to start with).
"""

from __future__ import annotations

import io
import json
import ssl
import subprocess
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any

CONF_DIR = "/opt/nifi/nifi-current/conf"
SENSITIVE_PROPS_KEY = "flowport-fixtures-key"
USERNAME = "flowport"
PASSWORD = "flowport-password-12"
STARTUP_TIMEOUT = 600


def docker(*args: str, stdin: bytes | None = None, check: bool = True) -> str:
    completed = subprocess.run(
        ["docker", *args], input=stdin, capture_output=True, check=False, text=stdin is None
    )
    if check and completed.returncode != 0:
        err = completed.stderr if isinstance(completed.stderr, str) else completed.stderr.decode()
        raise RuntimeError(f"docker {' '.join(args)} failed: {err}")
    out = completed.stdout
    return out if isinstance(out, str) else out.decode()


def owned_tar(files: dict[str, bytes], directories: tuple[str, ...] = ()) -> bytes:
    """A tar of files and directories owned by uid/gid 1000, the user NiFi runs as."""
    buffer = io.BytesIO()
    now = int(time.time())
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for directory in directories:
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            info.uid = info.gid = 1000
            info.mode = 0o755
            info.mtime = now
            tar.addfile(info)
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.uid = info.gid = 1000
            info.mode = 0o644
            info.mtime = now
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@dataclass(frozen=True)
class Component:
    name: str
    type: str
    status: str
    errors: frozenset[str]

    @classmethod
    def from_entity(cls, entity: dict[str, Any]) -> Component:
        component = entity["component"]
        return cls(
            name=component["name"],
            type=str(component.get("type", "")),
            status=str(component.get("validationStatus", "")),
            errors=frozenset(component.get("validationErrors") or []),
        )


@dataclass
class NiFi:
    """Minimal REST client; ``https`` switches on the 2.x token flow."""

    port: int
    https: bool = False
    token: str | None = field(default=None, repr=False)

    @property
    def base(self) -> str:
        scheme = "https" if self.https else "http"
        return f"{scheme}://localhost:{self.port}/nifi-api"

    def _context(self) -> ssl.SSLContext | None:
        if not self.https:
            return None
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        *,
        content_type: str | None = None,
        raw: bool = False,
    ) -> Any:
        headers = {"Accept": "*/*"}
        if content_type:
            headers["Content-Type"] = content_type
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60, context=self._context()) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from None
        if raw or not payload:
            return payload
        return json.loads(payload)

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post_json(self, path: str, body: Any) -> Any:
        return self.request(
            "POST", path, json.dumps(body).encode(), content_type="application/json"
        )

    def login(self) -> None:
        form = urllib.parse.urlencode({"username": USERNAME, "password": PASSWORD}).encode()
        token = self.request(
            "POST",
            "/access/token",
            form,
            content_type="application/x-www-form-urlencoded",
            raw=True,
        )
        self.token = token.decode()

    def wait_ready(self, container: str) -> None:
        deadline = time.monotonic() + STARTUP_TIMEOUT
        last = ""
        while time.monotonic() < deadline:
            try:
                if self.https and not self.token:
                    self.login()
                self.get("/flow/process-groups/root")
                return
            except (urllib.error.URLError, ConnectionError, OSError, RuntimeError) as exc:
                last = str(exc)
            status = docker("ps", "-a", "--filter", f"name={container}", "--format", "{{.Status}}")
            if "Exited" in status:
                raise RuntimeError(
                    f"{container} exited:\n{docker('logs', '--tail', '50', container)}"
                )
            time.sleep(5)
        raise RuntimeError(f"NiFi in {container} not ready after {STARTUP_TIMEOUT}s: {last}")

    # -- canvas queries -----------------------------------------------------

    def group_flow(self, group_id: str) -> dict[str, Any]:
        return self.get(f"/flow/process-groups/{group_id}")["processGroupFlow"]["flow"]

    def components(self, group_id: str = "root") -> dict[str, Component]:
        """Processors and controller services below a group, by id, with validation state."""
        found: dict[str, Component] = {}

        def visit(gid: str) -> None:
            flow = self.group_flow(gid)
            for entity in flow["processors"]:
                found[entity["id"]] = Component.from_entity(entity)
            services = self.get(f"/flow/process-groups/{gid}/controller-services")
            for entity in services["controllerServices"]:
                found.setdefault(entity["id"], Component.from_entity(entity))
            for child in flow["processGroups"]:
                visit(child["id"])

        visit(group_id)
        return found

    def wait_validated(self, group_id: str = "root") -> dict[str, Component]:
        deadline = time.monotonic() + 120
        while True:
            components = self.components(group_id)
            if not any(c.status == "VALIDATING" for c in components.values()):
                return components
            if time.monotonic() > deadline:
                raise RuntimeError("components still validating")
            time.sleep(3)

    def groups(self, group_id: str = "root") -> dict[str, dict[str, Any]]:
        """Process groups below a group, by name, with their parameter context name."""
        found: dict[str, dict[str, Any]] = {}

        def visit(gid: str) -> None:
            for child in self.group_flow(gid)["processGroups"]:
                component = child["component"]
                reference = component.get("parameterContext") or {}
                found[component["name"]] = {
                    "id": child["id"],
                    "context": (reference.get("component") or {}).get("name"),
                }
                visit(child["id"])

        visit(group_id)
        return found

    def count_components(self, group_id: str) -> dict[str, int]:
        """Component counts below a group, keyed like a flow definition."""
        counts = {
            "processors": 0,
            "controllerServices": 0,
            "connections": 0,
            "inputPorts": 0,
            "outputPorts": 0,
            "funnels": 0,
            "labels": 0,
            "remoteProcessGroups": 0,
            "processGroups": 0,
        }

        def visit(gid: str) -> None:
            flow = self.group_flow(gid)
            for key in counts:
                if key != "controllerServices":
                    counts[key] += len(flow.get(key) or [])
            services = self.get(f"/flow/process-groups/{gid}/controller-services")
            counts["controllerServices"] += sum(
                1 for s in services["controllerServices"] if s["parentGroupId"] == gid
            )
            for child in flow["processGroups"]:
                visit(child["id"])

        visit(group_id)
        return counts

    def upload_definition(self, parent_id: str, name: str, definition: bytes) -> str:
        """``Upload flow definition``: returns the id of the new process group."""
        boundary = f"----flowport{uuid.uuid4().hex}"
        fields = {
            "groupName": name,
            "positionX": "0",
            "positionY": "0",
            "clientId": "flowport-tests",
            "disconnectedNodeAcknowledged": "false",
        }
        parts: list[bytes] = []
        for key, value in fields.items():
            disposition = f'Content-Disposition: form-data; name="{key}"'
            parts.append(f"--{boundary}\r\n{disposition}\r\n\r\n{value}\r\n".encode())
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="{name}.json"\r\nContent-Type: application/json\r\n\r\n'
            ).encode()
            + definition
            + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode())
        entity = self.request(
            "POST",
            f"/process-groups/{parent_id}/process-groups/upload",
            b"".join(parts),
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        return str(entity["id"])


@dataclass
class Instance:
    container: str
    api: NiFi


def start(
    container: str,
    image: str,
    port: int,
    *,
    flow_gz: bytes | None = None,
    https: bool = False,
    directories: tuple[str, ...] = (),
) -> Instance:
    docker("rm", "-f", container, check=False)
    env = ["-e", f"NIFI_SENSITIVE_PROPS_KEY={SENSITIVE_PROPS_KEY}"]
    if https:
        env += [
            "-e",
            f"NIFI_WEB_HTTPS_PORT={port}",
            "-e",
            "NIFI_WEB_HTTPS_HOST=0.0.0.0",
            "-e",
            f"SINGLE_USER_CREDENTIALS_USERNAME={USERNAME}",
            "-e",
            f"SINGLE_USER_CREDENTIALS_PASSWORD={PASSWORD}",
        ]
    else:
        env += ["-e", f"NIFI_WEB_HTTP_PORT={port}", "-e", "NIFI_WEB_HTTP_HOST=0.0.0.0"]
    docker("create", "--name", container, "-p", f"{port}:{port}", *env, image)
    if flow_gz is not None:
        docker("cp", "-", f"{container}:{CONF_DIR}", stdin=owned_tar({"flow.json.gz": flow_gz}))
    if directories:
        docker("cp", "-", f"{container}:/", stdin=owned_tar({}, directories))
    docker("start", container)
    return Instance(container, NiFi(port, https=https))
