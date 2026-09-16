"""Read NiFi 1.x templates from XML files and from ``flow.json``.

Both sources hold the same object: a ``TemplateDTO`` with a ``FlowSnippetDTO``.
``flow.json`` stores it as JSON under ``templates[].templateDto``; the XML
export is the JAXB rendering of the same DTO, where lists are repeated
elements and maps are ``<entry><key/><value/></entry>`` sequences. The reader
turns the XML into the JSON shape so that one converter serves both.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from flowport.loaders import LoadError

# JAXB writes list members as repeated elements without a wrapper, so a list
# with one member looks like a scalar. These element names are always lists.
LIST_TAGS = frozenset(
    {
        "processors",
        "connections",
        "processGroups",
        "remoteProcessGroups",
        "inputPorts",
        "outputPorts",
        "funnels",
        "labels",
        "controllerServices",
        "relationships",
        "selectedRelationships",
        "autoTerminatedRelationships",
        "retriedRelationships",
        "prioritizers",
        "bends",
        "dependencies",
        "dependentValues",
        "allowableValues",
        "entry",
    }
)
# Elements whose children are ``entry`` pairs.
MAP_TAGS = frozenset({"properties", "descriptors", "style", "variables", "batchSettings"})


@dataclass(frozen=True)
class TemplateSource:
    """A template as found in a file, before conversion."""

    name: str
    description: str
    encoding_version: str
    snippet: dict[str, Any]
    group_id: str | None = None
    template_id: str | None = None


def parse_template_xml(data: bytes) -> TemplateSource:
    """Parse an exported ``<template>`` document."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise LoadError(f"template XML is not well-formed: {exc}") from exc
    if root.tag != "template":
        raise LoadError(f"XML root element is <{root.tag}>, expected <template>")
    dto = _element_to_value(root)
    if not isinstance(dto, dict) or not isinstance(dto.get("snippet"), dict):
        raise LoadError("template XML has no <snippet>")
    return TemplateSource(
        name=str(dto.get("name", "")),
        description=str(dto.get("description") or ""),
        encoding_version=str(root.get("encoding-version", "")),
        snippet=dto["snippet"],
        group_id=dto.get("groupId"),
        template_id=dto.get("id"),
    )


def templates_in_flow(document: dict[str, Any]) -> list[TemplateSource]:
    """Every template stored in a ``flow.json`` document, in document order."""
    found: list[TemplateSource] = []
    for entry in document.get("templates") or []:
        if not isinstance(entry, dict):
            continue
        dto = entry.get("templateDto")
        if not isinstance(dto, dict) or not isinstance(dto.get("snippet"), dict):
            continue
        found.append(
            TemplateSource(
                name=str(dto.get("name") or entry.get("name") or ""),
                description=str(dto.get("description") or ""),
                encoding_version=str(dto.get("encoding-version", "")),
                snippet=dto["snippet"],
                group_id=entry.get("groupIdentifier"),
                template_id=str(dto.get("id") or entry.get("identifier") or "") or None,
            )
        )
    return found


def _element_to_value(element: ET.Element) -> Any:
    children = list(element)
    if not children:
        return {} if element.tag in MAP_TAGS else (element.text or "")
    if element.tag in MAP_TAGS:
        mapping: dict[str, Any] = {}
        for entry in children:
            key = entry.find("key")
            value = entry.find("value")
            if key is None:
                continue
            mapping[key.text or ""] = None if value is None else _element_to_value(value)
        return mapping
    result: dict[str, Any] = {}
    for child in children:
        value = _element_to_value(child)
        if child.tag in LIST_TAGS or child.tag in result:
            existing = result.get(child.tag)
            if existing is None:
                result[child.tag] = [value]
            elif isinstance(existing, list):
                existing.append(value)
            else:
                result[child.tag] = [existing, value]
        else:
            result[child.tag] = value
    return result
