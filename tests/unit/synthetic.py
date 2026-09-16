"""Builders for small synthetic flow documents used by unit tests."""

from typing import Any

STD = "org.apache.nifi.processors.standard."
APACHE = {"group": "org.apache.nifi", "artifact": "nifi-standard-nar", "version": "1.28.1"}


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


def document(root: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """A minimal flow.json document around ``root``."""
    doc: dict[str, Any] = {"encodingVersion": {"majorVersion": 2, "minorVersion": 0}}
    doc.update(extra)
    doc["rootGroup"] = root
    return doc
