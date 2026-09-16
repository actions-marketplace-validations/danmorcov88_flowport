"""Read flow files into the model.

Input type is detected from content, never from the file name:

- gzip magic bytes -> decompress first
- JSON with ``rootGroup``     -> ``flow.json`` (whole canvas)
- JSON with ``flowContents``  -> flow definition (exported group / registry snapshot)
- XML ``<template``           -> XML template (supported from Phase 3)
- XML ``<flowController``     -> ``flow.xml`` (not supported; upgrade to the last 1.x first)
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from flowport.model import (
    Component,
    ComponentKind,
    Flow,
    InputKind,
    ProcessGroup,
    Template,
    parameter_contexts_from_raw,
)

MIGRATION_GUIDANCE_URL = "https://cwiki.apache.org/confluence/display/NIFI/Migration+Guidance"
GZIP_MAGIC = b"\x1f\x8b"


class LoadError(Exception):
    """The input could not be read as a supported flow file."""


class UnsupportedInputError(LoadError):
    """The input was recognized but this version of the tool does not handle it."""


def read_bytes(path: Path) -> bytes:
    """Read a file, transparently decompressing gzip."""
    data = path.read_bytes()
    if data[:2] == GZIP_MAGIC:
        try:
            return gzip.decompress(data)
        except (OSError, EOFError) as exc:
            raise LoadError(f"{path}: gzip data is corrupt: {exc}") from exc
    return data


def detect_kind(data: bytes) -> InputKind:
    """Classify decompressed content."""
    head = data.lstrip()[:4096]
    if head.startswith(b"<") or head.startswith(b"\xef\xbb\xbf<"):
        if b"<template" in head:
            return InputKind.TEMPLATE
        if b"<flowController" in head:
            return InputKind.FLOW_XML
        raise LoadError("XML input is not a NiFi template or flow.xml")
    if head.startswith(b"{") or head.startswith(b"\xef\xbb\xbf{"):
        try:
            document = json.loads(data)
        except ValueError as exc:
            raise LoadError(f"invalid JSON: {exc}") from exc
        if not isinstance(document, dict):
            raise LoadError("JSON input is not an object")
        if "rootGroup" in document:
            return InputKind.FLOW
        if "flowContents" in document:
            return InputKind.DEFINITION
        raise LoadError(
            "JSON input has neither 'rootGroup' (flow.json) nor 'flowContents' (flow definition)"
        )
    raise LoadError("input is not gzip, JSON or XML")


def load(path: Path) -> Flow:
    """Load a flow file. Raises LoadError for unreadable or unsupported input."""
    if not path.is_file():
        raise LoadError(f"{path}: no such file")
    data = read_bytes(path)
    kind = detect_kind(data)
    if kind is InputKind.FLOW_XML:
        raise UnsupportedInputError(
            f"{path} is a flow.xml file. flowport reads flow.json.gz, which NiFi 1.16 and later "
            "write. Upgrade to the last 1.x release first, start it once so it converts the "
            f"flow to flow.json.gz, then run flowport on that file. See {MIGRATION_GUIDANCE_URL}"
        )
    if kind is InputKind.TEMPLATE:
        raise UnsupportedInputError(
            f"{path} is an XML template. Template conversion is not implemented yet; analyze "
            "the flow.json.gz of the instance that holds the template instead."
        )
    document = json.loads(data)
    flow = (
        load_flow_document(document)
        if kind is InputKind.FLOW
        else load_definition_document(document)
    )
    flow.source_name = path.name
    return flow


def load_flow_document(document: dict[str, Any]) -> Flow:
    """Build the model from a parsed flow.json object."""
    root = ProcessGroup.from_raw(document["rootGroup"], "")
    return Flow(
        raw=document,
        kind=InputKind.FLOW,
        root=root,
        parameter_contexts=parameter_contexts_from_raw(document.get("parameterContexts")),
        controller_services=[
            Component.from_raw(ComponentKind.CONTROLLER_SERVICE, s, "")
            for s in document.get("controllerServices") or []
        ],
        reporting_tasks=[
            Component.from_raw(ComponentKind.REPORTING_TASK, r, "")
            for r in document.get("reportingTasks") or []
        ],
        templates=[
            Template(
                raw=t,
                id=str(t.get("instanceIdentifier") or t.get("identifier") or ""),
                name=str(t.get("name", "")),
                path="",
            )
            for t in document.get("templates") or []
        ],
    )


def load_definition_document(document: dict[str, Any]) -> Flow:
    """Build the model from a parsed flow definition object."""
    root = ProcessGroup.from_raw(document["flowContents"], "")
    return Flow(
        raw=document,
        kind=InputKind.DEFINITION,
        root=root,
        parameter_contexts=parameter_contexts_from_raw(document.get("parameterContexts")),
        controller_services=[
            Component.from_raw(ComponentKind.CONTROLLER_SERVICE, s, "")
            for s in document.get("externalControllerServices") or []
            if isinstance(s, dict)
        ],
        reporting_tasks=[],
        templates=[],
    )
