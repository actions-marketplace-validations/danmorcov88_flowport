"""Validate a flow against a running NiFi: import it and collect what NiFi says.

The flow (a whole ``flow.json`` or a flow definition) is uploaded as a new
process group through the endpoint behind "Upload flow definition", the
components are left to validate, and their state is collected. The group is
deleted afterwards unless the caller keeps it. A ``flow.json`` is wrapped
into a definition: its root group becomes the uploaded group and its
parameter contexts travel with it; controller-level services and reporting
tasks cannot be imported this way and are reported as warnings.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from flowport.model import Flow, InputKind
from flowport.nifi import ComponentState, NiFiClient, NiFiError

FLOW_ENCODING_VERSION = "1.0"


@dataclass
class ValidationResult:
    nifi_url: str
    nifi_version: str
    group_id: str
    group_name: str
    kept: bool
    components: list[ComponentState] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def invalid(self) -> list[ComponentState]:
        return [c for c in self.components if not c.valid]

    def summary(self) -> dict[str, int]:
        return {
            "components": len(self.components),
            "valid": sum(1 for c in self.components if c.valid),
            "invalid": sum(1 for c in self.components if not c.valid and not c.ghost),
            "ghosts": sum(1 for c in self.components if c.ghost),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "nifi": {"url": self.nifi_url, "version": self.nifi_version},
            "group": {"id": self.group_id, "name": self.group_name, "kept": self.kept},
            "warnings": list(self.warnings),
            "summary": self.summary(),
            "components": [
                {
                    "id": c.id,
                    "name": c.name,
                    "type": c.type,
                    "kind": c.kind,
                    "path": c.path,
                    "status": c.status,
                    "ghost": c.ghost,
                    "errors": list(c.errors),
                }
                for c in sorted(self.components, key=lambda c: (c.path, c.name, c.id))
            ],
        }


def definition_for_validation(flow: Flow) -> tuple[dict[str, Any], list[str]]:
    """The document to upload, and warnings about what a group upload cannot carry."""
    warnings: list[str] = []
    if flow.kind is InputKind.DEFINITION:
        return copy.deepcopy(flow.raw), warnings
    document = flow.raw
    root = copy.deepcopy(document["rootGroup"])
    root.pop("templates", None)
    contexts: dict[str, Any] = {}
    for entry in document.get("parameterContexts") or []:
        if isinstance(entry, dict) and entry.get("name"):
            contexts[str(entry["name"])] = copy.deepcopy(entry)
    if document.get("templates"):
        warnings.append(
            f"{len(document['templates'])} template(s) are not imported; NiFi 2.x drops "
            "them (convert them with 'migrate templates')."
        )
    services = [s.get("name", "?") for s in document.get("controllerServices") or []]
    if services:
        warnings.append(
            "controller-level controller services are not imported with a process group: "
            + ", ".join(str(s) for s in services)
        )
    tasks = [t.get("name", "?") for t in document.get("reportingTasks") or []]
    if tasks:
        warnings.append(
            "reporting tasks are not imported with a process group: "
            + ", ".join(str(t) for t in tasks)
        )
    return {
        "flowContents": root,
        "externalControllerServices": {},
        "parameterContexts": contexts,
        "flowEncodingVersion": FLOW_ENCODING_VERSION,
        "parameterProviders": {},
        "latest": False,
    }, warnings


def validate_flow(
    flow: Flow,
    client: NiFiClient,
    *,
    group_name: str | None = None,
    keep: bool = False,
    timeout: float = 120,
) -> ValidationResult:
    """Upload the flow as a process group, wait for validation, collect the state."""
    document, warnings = definition_for_validation(flow)
    name = group_name or f"flowport validation of {flow.source_name or 'flow'}"
    client.login()
    version = str(client.about().get("version", ""))
    group_id = client.upload_definition("root", name, document)
    try:
        components = client.wait_validated(group_id, timeout=timeout)
    finally:
        if not keep:
            try:
                client.delete_group(group_id)
            except NiFiError as exc:
                warnings.append(f"could not delete the validation group {group_id}: {exc}")
    return ValidationResult(
        nifi_url=client.base_url,
        nifi_version=version,
        group_id=group_id,
        group_name=name,
        kept=keep,
        components=sorted(components.values(), key=lambda c: (c.path, c.name, c.id)),
        warnings=warnings,
    )
