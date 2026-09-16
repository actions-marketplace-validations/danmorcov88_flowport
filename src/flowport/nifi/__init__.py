"""A small REST client for Apache NiFi, used by ``flowport validate``.

Standard library only. NiFi 1.x speaks plain HTTP; NiFi 2.x is HTTPS with
single-user credentials exchanged for a bearer token at ``/access/token``.
Nothing here runs unless the user asks for validation: the analyzer and the
migrations stay offline.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

CLIENT_ID = "flowport"


class NiFiError(Exception):
    """The NiFi API refused a request or is not reachable."""


@dataclass(frozen=True)
class ComponentState:
    """Validation state of a processor or controller service on the canvas."""

    id: str
    name: str
    type: str
    kind: str
    path: str
    status: str
    errors: tuple[str, ...]
    ghost: bool

    @property
    def valid(self) -> bool:
        return self.status == "VALID" and not self.ghost


class NiFiClient:
    def __init__(
        self,
        base_url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        token: str | None = None,
        verify_tls: bool = True,
        timeout: float = 60,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/nifi-api"):
            self.base_url += "/nifi-api"
        self.username = username
        self.password = password
        self.token = token
        self.timeout = timeout
        self._context: ssl.SSLContext | None = None
        if self.base_url.startswith("https://"):
            self._context = ssl.create_default_context()
            if not verify_tls:
                self._context.check_hostname = False
                self._context.verify_mode = ssl.CERT_NONE

    # -- HTTP -----------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        *,
        content_type: str | None = None,
        raw: bool = False,
    ) -> Any:
        headers = {"Accept": "*/*", "User-Agent": "flowport"}
        if content_type:
            headers["Content-Type"] = content_type
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(
            self.base_url + path, data=body, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._context) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace").strip()
            raise NiFiError(f"{method} {path} -> HTTP {exc.code}: {detail[:500]}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise NiFiError(f"{method} {path}: {exc}") from None
        if raw or not payload:
            return payload
        return json.loads(payload)

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def login(self) -> None:
        """Exchange the credentials for a bearer token (NiFi 2.x, single user)."""
        if not (self.username and self.password):
            return
        form = urllib.parse.urlencode({"username": self.username, "password": self.password})
        token = self.request(
            "POST",
            "/access/token",
            form.encode(),
            content_type="application/x-www-form-urlencoded",
            raw=True,
        )
        self.token = token.decode()

    def wait_ready(self, timeout: float = 600, *, poll: float = 5, probe: Any = None) -> None:
        """Poll until the root group answers; ``probe()`` may raise to abort early."""
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            try:
                if not self.token:
                    self.login()
                self.get("/flow/process-groups/root")
                return
            except NiFiError as exc:
                last = str(exc)
            if probe is not None:
                probe()
            time.sleep(poll)
        raise NiFiError(f"NiFi at {self.base_url} not ready after {timeout:.0f}s: {last}")

    # -- canvas ---------------------------------------------------------------

    def about(self) -> dict[str, Any]:
        return dict(self.get("/flow/about")["about"])

    def root_id(self) -> str:
        return str(self.get("/process-groups/root")["id"])

    def group_flow(self, group_id: str) -> dict[str, Any]:
        return dict(self.get(f"/flow/process-groups/{group_id}")["processGroupFlow"]["flow"])

    def components(self, group_id: str = "root") -> dict[str, ComponentState]:
        """Processors and controller services below a group, by id, with validation state."""
        found: dict[str, ComponentState] = {}

        def visit(gid: str, path: str) -> None:
            flow = self.group_flow(gid)
            for entity in flow["processors"]:
                found[entity["id"]] = _state(entity, "PROCESSOR", path)
            services = self.get(f"/flow/process-groups/{gid}/controller-services")
            for entity in services["controllerServices"]:
                if entity.get("parentGroupId") == gid or gid == "root":
                    found.setdefault(entity["id"], _state(entity, "CONTROLLER_SERVICE", path))
            for child in flow["processGroups"]:
                visit(child["id"], f"{path}/{child['component']['name']}")

        name = (
            ""
            if group_id == "root"
            else self.get(f"/process-groups/{group_id}")["component"]["name"]
        )
        visit(group_id, f"/{name}" if name else "")
        return found

    def wait_validated(
        self, group_id: str = "root", timeout: float = 120
    ) -> dict[str, ComponentState]:
        deadline = time.monotonic() + timeout
        while True:
            components = self.components(group_id)
            if not any(c.status == "VALIDATING" for c in components.values()):
                return components
            if time.monotonic() > deadline:
                raise NiFiError("components are still validating")
            time.sleep(3)

    def count_components(self, group_id: str) -> dict[str, int]:
        """Component counts below a group, keyed like a flow definition."""
        counts = dict.fromkeys(
            (
                "processors",
                "controllerServices",
                "connections",
                "inputPorts",
                "outputPorts",
                "funnels",
                "labels",
                "remoteProcessGroups",
                "processGroups",
            ),
            0,
        )

        def visit(gid: str) -> None:
            flow = self.group_flow(gid)
            for key in counts:
                if key != "controllerServices":
                    counts[key] += len(flow.get(key) or [])
            services = self.get(f"/flow/process-groups/{gid}/controller-services")
            counts["controllerServices"] += sum(
                1 for s in services["controllerServices"] if s.get("parentGroupId") == gid
            )
            for child in flow["processGroups"]:
                visit(child["id"])

        visit(group_id)
        return counts

    def groups(self, group_id: str = "root") -> dict[str, dict[str, Any]]:
        """Process groups below a group, by name, with id and parameter context name."""
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

    def parameter_contexts(self) -> dict[str, dict[str, Any]]:
        entities = self.get("/flow/parameter-contexts")["parameterContexts"]
        return {e["component"]["name"]: e for e in entities}

    def upload_definition(self, parent_id: str, name: str, document: dict[str, Any]) -> str:
        """``Upload flow definition``: returns the id of the new process group."""
        boundary = f"----flowport{uuid.uuid4().hex}"
        fields = {
            "groupName": name,
            "positionX": "0",
            "positionY": "0",
            "clientId": CLIENT_ID,
            "disconnectedNodeAcknowledged": "false",
        }
        crlf = "\r\n"
        parts: list[bytes] = []
        for key, value in fields.items():
            disposition = f'Content-Disposition: form-data; name="{key}"'
            parts.append(f"--{boundary}{crlf}{disposition}{crlf}{crlf}{value}{crlf}".encode())
        file_head = (
            f'--{boundary}{crlf}Content-Disposition: form-data; name="file"; '
            f'filename="{name}.json"{crlf}Content-Type: application/json{crlf}{crlf}'
        )
        parts.append(file_head.encode() + json.dumps(document).encode("utf-8") + crlf.encode())
        parts.append(f"--{boundary}--{crlf}".encode())
        entity = self.request(
            "POST",
            f"/process-groups/{parent_id}/process-groups/upload",
            b"".join(parts),
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        return str(entity["id"])

    def delete_group(self, group_id: str, timeout: float = 60) -> None:
        """Stop the group, disable its controller services, then delete it.

        NiFi refuses to delete a group with running processors or enabled
        services; an imported flow may hold enabled services that NiFi itself
        created while migrating deprecated properties.
        """
        self.request(
            "PUT",
            f"/flow/process-groups/{group_id}",
            json.dumps({"id": group_id, "state": "STOPPED"}).encode(),
            content_type="application/json",
        )
        self.request(
            "PUT",
            f"/flow/process-groups/{group_id}/controller-services",
            json.dumps(
                {"id": group_id, "state": "DISABLED", "disconnectedNodeAcknowledged": False}
            ).encode(),
            content_type="application/json",
        )
        deadline = time.monotonic() + timeout
        while True:
            services = self.get(f"/flow/process-groups/{group_id}/controller-services")
            states = {s["component"].get("state") for s in services["controllerServices"]}
            if states <= {"DISABLED"}:
                break
            if time.monotonic() > deadline:
                raise NiFiError(f"controller services of {group_id} did not disable: {states}")
            time.sleep(2)
        entity = self.get(f"/process-groups/{group_id}")
        version = entity["revision"]["version"]
        query = urllib.parse.urlencode(
            {"version": version, "clientId": CLIENT_ID, "disconnectedNodeAcknowledged": "false"}
        )
        self.request("DELETE", f"/process-groups/{group_id}?{query}")


def _state(entity: dict[str, Any], kind: str, path: str) -> ComponentState:
    component = entity["component"]
    return ComponentState(
        id=str(entity["id"]),
        name=str(component.get("name", "")),
        type=str(component.get("type", "")),
        kind=kind,
        path=path,
        status=str(component.get("validationStatus", "")),
        errors=tuple(sorted(component.get("validationErrors") or [])),
        ghost=bool(component.get("extensionMissing", False)),
    )
