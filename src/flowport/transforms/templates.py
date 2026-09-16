"""Convert a NiFi 1.x template to a flow definition.

NiFi 2.x removed templates (NIFI-12006). The replacement is the flow
definition: the JSON that "Download flow definition" writes and "Upload flow
definition" reads, a versioned process group as stored in NiFi Registry.

A template is a ``FlowSnippetDTO``: the components of one process group, in
the REST API's DTO shape (``config`` blocks, ``state``, ``contents`` on nested
groups). The converter maps each DTO to its versioned counterpart, field by
field, following the key order NiFi 1.28.1 uses in its own exports so that the
result is easy to compare with a real export. Template identifiers become the
versioned identifiers; they are unique within the template and the properties
that reference controller services already use them.

What is not carried over: nothing a flow definition can hold. Sensitive
values are absent from templates to begin with, and NiFi assigns new
instance identifiers on import.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from flowport.catalog import Catalog
from flowport.loaders import load_definition_document
from flowport.loaders.templates import TemplateSource
from flowport.model import Flow
from flowport.rules import Finding, analyze

FLOW_ENCODING_VERSION = "1.0"
NAMESPACE = uuid.UUID("c0231dd3-8a1d-532c-8217-c4dcb4e6bc7f")


def convert_template(source: TemplateSource) -> dict[str, Any]:
    """The flow definition document for a template."""
    snippet = source.snippet
    root_id = _snippet_group_id(snippet) or str(uuid.uuid5(NAMESPACE, f"template:{source.name}"))
    names = _Names(snippet)
    root: dict[str, Any] = {
        "identifier": root_id,
        "name": source.name,
        "comments": source.description,
        "position": {"x": 0.0, "y": 0.0},
    }
    root.update(_contents(snippet, root_id, names))
    root["variables"] = {}
    root["componentType"] = "PROCESS_GROUP"
    return {
        "flowContents": root,
        "externalControllerServices": {},
        "parameterContexts": {},
        "flowEncodingVersion": FLOW_ENCODING_VERSION,
        "parameterProviders": {},
        "latest": False,
    }


def _snippet_group_id(snippet: dict[str, Any]) -> str | None:
    """The group every top-level component of the snippet belongs to."""
    for key in _CONTENT_KEYS:
        for dto in snippet.get(key) or []:
            if isinstance(dto, dict) and dto.get("parentGroupId"):
                return str(dto["parentGroupId"])
    return None


_CONTENT_KEYS = (
    "processGroups",
    "remoteProcessGroups",
    "processors",
    "inputPorts",
    "outputPorts",
    "connections",
    "labels",
    "funnels",
    "controllerServices",
)


class _Names:
    """Names of every connectable component, by id, to fill connection ends."""

    def __init__(self, snippet: dict[str, Any]) -> None:
        self.names: dict[str, str] = {}
        self._index(snippet)

    def _index(self, snippet: dict[str, Any]) -> None:
        for key in ("processors", "inputPorts", "outputPorts"):
            for dto in snippet.get(key) or []:
                self.names[str(dto.get("id"))] = str(dto.get("name", ""))
        for dto in snippet.get("funnels") or []:
            self.names[str(dto.get("id"))] = "Funnel"
        for dto in snippet.get("remoteProcessGroups") or []:
            contents = dto.get("contents") or {}
            for port in [*(contents.get("inputPorts") or []), *(contents.get("outputPorts") or [])]:
                self.names[str(port.get("id"))] = str(port.get("name", ""))
        for dto in snippet.get("processGroups") or []:
            self._index(dto.get("contents") or {})

    def get(self, component_id: str) -> str | None:
        return self.names.get(component_id)


def _contents(snippet: dict[str, Any], group_id: str, names: _Names) -> dict[str, Any]:
    return {
        "processGroups": [_group(g, group_id, names) for g in snippet.get("processGroups") or []],
        "remoteProcessGroups": [
            _remote_group(r, group_id) for r in snippet.get("remoteProcessGroups") or []
        ],
        "processors": [_processor(p, group_id) for p in snippet.get("processors") or []],
        "inputPorts": [_port(p, group_id, "INPUT_PORT") for p in snippet.get("inputPorts") or []],
        "outputPorts": [
            _port(p, group_id, "OUTPUT_PORT") for p in snippet.get("outputPorts") or []
        ],
        "connections": [_connection(c, group_id, names) for c in snippet.get("connections") or []],
        "labels": [_label(x, group_id) for x in snippet.get("labels") or []],
        "funnels": [_funnel(f, group_id) for f in snippet.get("funnels") or []],
        "controllerServices": [
            _controller_service(s, group_id) for s in snippet.get("controllerServices") or []
        ],
    }


def _group(dto: dict[str, Any], parent_id: str, names: _Names) -> dict[str, Any]:
    group_id = str(dto["id"])
    versioned: dict[str, Any] = {
        "identifier": group_id,
        "name": str(dto.get("name", "")),
        "comments": str(dto.get("comments") or ""),
        "position": _position(dto.get("position")),
    }
    versioned.update(_contents(dto.get("contents") or {}, group_id, names))
    versioned["variables"] = {str(k): str(v) for k, v in (dto.get("variables") or {}).items()}
    context = dto.get("parameterContext") or {}
    context_name = (context.get("component") or {}).get("name")
    if context_name:
        versioned["parameterContextName"] = str(context_name)
    _copy(versioned, dto, "defaultFlowFileExpiration")
    _copy(versioned, dto, "defaultBackPressureObjectThreshold", _int)
    _copy(versioned, dto, "defaultBackPressureDataSizeThreshold")
    _copy(versioned, dto, "flowfileConcurrency", target="flowFileConcurrency")
    _copy(versioned, dto, "flowfileOutboundPolicy", target="flowFileOutboundPolicy")
    versioned["componentType"] = "PROCESS_GROUP"
    versioned["groupIdentifier"] = parent_id
    return versioned


def _processor(dto: dict[str, Any], group_id: str) -> dict[str, Any]:
    config = dto.get("config") or {}
    versioned: dict[str, Any] = {
        "identifier": str(dto["id"]),
        "name": str(dto.get("name", "")),
        "comments": str(config.get("comments") or ""),
        "position": _position(dto.get("position")),
        "type": str(dto.get("type", "")),
        "bundle": _bundle(dto.get("bundle")),
        "properties": _properties(config.get("properties")),
        "propertyDescriptors": _descriptors(config.get("descriptors")),
        "style": dict(dto.get("style") or {}),
    }
    if config.get("annotationData"):
        versioned["annotationData"] = str(config["annotationData"])
    _copy(versioned, config, "schedulingPeriod")
    _copy(versioned, config, "schedulingStrategy")
    _copy(versioned, config, "executionNode")
    _copy(versioned, config, "penaltyDuration")
    _copy(versioned, config, "yieldDuration")
    _copy(versioned, config, "bulletinLevel")
    _copy(versioned, config, "runDurationMillis", _int)
    _copy(versioned, config, "concurrentlySchedulableTaskCount", _int)
    versioned["autoTerminatedRelationships"] = [
        str(r["name"])
        for r in dto.get("relationships") or []
        if isinstance(r, dict) and _bool(r.get("autoTerminate"))
    ]
    versioned["scheduledState"] = _scheduled_state(dto.get("state"))
    _copy(versioned, config, "retryCount", _int)
    versioned["retriedRelationships"] = [str(r) for r in config.get("retriedRelationships") or []]
    _copy(versioned, config, "backoffMechanism")
    _copy(versioned, config, "maxBackoffPeriod")
    versioned["componentType"] = "PROCESSOR"
    versioned["groupIdentifier"] = group_id
    return versioned


def _controller_service(dto: dict[str, Any], group_id: str) -> dict[str, Any]:
    versioned: dict[str, Any] = {
        "identifier": str(dto["id"]),
        "name": str(dto.get("name", "")),
        "comments": str(dto.get("comments") or ""),
        "type": str(dto.get("type", "")),
        "bundle": _bundle(dto.get("bundle")),
        "properties": _properties(dto.get("properties")),
        "propertyDescriptors": _descriptors(dto.get("descriptors")),
    }
    if dto.get("annotationData"):
        versioned["annotationData"] = str(dto["annotationData"])
    versioned["scheduledState"] = _scheduled_state(dto.get("state"))
    _copy(versioned, dto, "bulletinLevel")
    versioned["componentType"] = "CONTROLLER_SERVICE"
    versioned["groupIdentifier"] = group_id
    return versioned


def _port(dto: dict[str, Any], group_id: str, port_type: str) -> dict[str, Any]:
    versioned: dict[str, Any] = {
        "identifier": str(dto["id"]),
        "name": str(dto.get("name", "")),
    }
    if dto.get("comments"):
        versioned["comments"] = str(dto["comments"])
    versioned["position"] = _position(dto.get("position"))
    versioned["type"] = port_type
    _copy(versioned, dto, "concurrentlySchedulableTaskCount", _int)
    versioned["scheduledState"] = _scheduled_state(dto.get("state"))
    versioned["allowRemoteAccess"] = _bool(dto.get("allowRemoteAccess"))
    versioned["componentType"] = port_type
    versioned["groupIdentifier"] = group_id
    return versioned


def _connection(dto: dict[str, Any], group_id: str, names: _Names) -> dict[str, Any]:
    versioned: dict[str, Any] = {"identifier": str(dto["id"])}
    if dto.get("name"):
        versioned["name"] = str(dto["name"])
    versioned["source"] = _connectable(dto.get("source") or {}, names)
    versioned["destination"] = _connectable(dto.get("destination") or {}, names)
    _copy(versioned, dto, "labelIndex", _int)
    _copy(versioned, dto, "zIndex", _int)
    relationships = dto.get("selectedRelationships")
    versioned["selectedRelationships"] = [str(r) for r in relationships] if relationships else [""]
    _copy(versioned, dto, "backPressureObjectThreshold", _int)
    _copy(versioned, dto, "backPressureDataSizeThreshold")
    _copy(versioned, dto, "flowFileExpiration")
    versioned["prioritizers"] = [str(p) for p in dto.get("prioritizers") or []]
    versioned["bends"] = [_position(b) for b in dto.get("bends") or []]
    _copy(versioned, dto, "loadBalanceStrategy")
    if dto.get("loadBalancePartitionAttribute"):
        versioned["partitioningAttribute"] = str(dto["loadBalancePartitionAttribute"])
    _copy(versioned, dto, "loadBalanceCompression")
    versioned["componentType"] = "CONNECTION"
    versioned["groupIdentifier"] = group_id
    return versioned


def _connectable(dto: dict[str, Any], names: _Names) -> dict[str, Any]:
    component_id = str(dto.get("id", ""))
    end: dict[str, Any] = {
        "id": component_id,
        "type": str(dto.get("type", "")),
        "groupId": str(dto.get("groupId", "")),
    }
    name = dto.get("name") or names.get(component_id)
    if name is not None:
        end["name"] = str(name)
    return end


def _label(dto: dict[str, Any], group_id: str) -> dict[str, Any]:
    versioned: dict[str, Any] = {
        "identifier": str(dto["id"]),
        "position": _position(dto.get("position")),
        "label": str(dto.get("label") or ""),
    }
    _copy(versioned, dto, "zIndex", _int)
    _copy(versioned, dto, "width", _float)
    _copy(versioned, dto, "height", _float)
    versioned["style"] = dict(dto.get("style") or {})
    versioned["componentType"] = "LABEL"
    versioned["groupIdentifier"] = group_id
    return versioned


def _funnel(dto: dict[str, Any], group_id: str) -> dict[str, Any]:
    return {
        "identifier": str(dto["id"]),
        "position": _position(dto.get("position")),
        "componentType": "FUNNEL",
        "groupIdentifier": group_id,
    }


def _remote_group(dto: dict[str, Any], group_id: str) -> dict[str, Any]:
    remote_id = str(dto["id"])
    # Templates carry no name for a remote group (NiFi shows the remote
    # instance's name); the target URI is the readable, stable fallback.
    versioned: dict[str, Any] = {
        "identifier": remote_id,
        "name": str(dto.get("name") or dto.get("targetUris") or dto.get("targetUri") or ""),
        "comments": str(dto.get("comments") or ""),
    }
    versioned["position"] = _position(dto.get("position"))
    _copy(versioned, dto, "targetUri")
    _copy(versioned, dto, "targetUris")
    _copy(versioned, dto, "communicationsTimeout")
    _copy(versioned, dto, "yieldDuration")
    _copy(versioned, dto, "transportProtocol")
    _copy(versioned, dto, "localNetworkInterface")
    _copy(versioned, dto, "proxyHost")
    _copy(versioned, dto, "proxyPort", _int)
    _copy(versioned, dto, "proxyUser")
    contents = dto.get("contents") or {}
    versioned["inputPorts"] = [
        _remote_port(p, remote_id, "REMOTE_INPUT_PORT") for p in contents.get("inputPorts") or []
    ]
    versioned["outputPorts"] = [
        _remote_port(p, remote_id, "REMOTE_OUTPUT_PORT") for p in contents.get("outputPorts") or []
    ]
    versioned["componentType"] = "REMOTE_PROCESS_GROUP"
    versioned["groupIdentifier"] = group_id
    return versioned


def _remote_port(dto: dict[str, Any], remote_id: str, port_type: str) -> dict[str, Any]:
    versioned: dict[str, Any] = {
        "identifier": str(dto["id"]),
        "name": str(dto.get("name", "")),
        "remoteGroupId": remote_id,
    }
    _copy(versioned, dto, "concurrentlySchedulableTaskCount", _int)
    versioned["useCompression"] = _bool(dto.get("useCompression"))
    batch = dto.get("batchSettings") or {}
    batch_size: dict[str, Any] = {}
    _copy(batch_size, batch, "count", _int)
    _copy(batch_size, batch, "size")
    _copy(batch_size, batch, "duration")
    versioned["batchSize"] = batch_size
    versioned["componentType"] = port_type
    _copy(versioned, dto, "targetId")
    # Versioned remote ports are ENABLED unless disabled; whether they transmit
    # is runtime state (NiFi's own export writes ENABLED for a stopped port).
    versioned["scheduledState"] = "ENABLED"
    versioned["groupIdentifier"] = remote_id
    return versioned


# -- field helpers ----------------------------------------------------------


def _copy(
    into: dict[str, Any],
    source: dict[str, Any],
    key: str,
    convert: Any = str,
    *,
    target: str | None = None,
) -> None:
    """Copy ``source[key]`` into ``into`` (as ``target`` or ``key``) when present and not null."""
    value = source.get(key)
    if value is not None:
        into[target or key] = convert(value)


def _position(dto: Any) -> dict[str, float]:
    dto = dto or {}
    return {"x": _float(dto.get("x", 0.0)), "y": _float(dto.get("y", 0.0))}


def _bundle(dto: Any) -> dict[str, str]:
    dto = dto or {}
    return {
        "group": str(dto.get("group", "")),
        "artifact": str(dto.get("artifact", "")),
        "version": str(dto.get("version", "")),
    }


def _properties(dto: Any) -> dict[str, str]:
    """Property values; unset ones (null in XML, absent in JSON) are dropped."""
    return {str(k): str(v) for k, v in (dto or {}).items() if v is not None}


def _descriptors(dto: Any) -> dict[str, dict[str, Any]]:
    descriptors: dict[str, dict[str, Any]] = {}
    for key, descriptor in (dto or {}).items():
        descriptor = descriptor or {}
        name = str(descriptor.get("name") or key)
        descriptors[str(key)] = {
            "name": name,
            "displayName": str(descriptor.get("displayName") or name),
            "identifiesControllerService": bool(descriptor.get("identifiesControllerService")),
            "sensitive": _bool(descriptor.get("sensitive")),
        }
    return descriptors


def _scheduled_state(state: Any) -> str:
    """Versioned components are ENABLED or DISABLED; running and stopped are both enabled."""
    return "DISABLED" if str(state or "").upper() == "DISABLED" else "ENABLED"


def _int(value: Any) -> int:
    return int(float(value)) if isinstance(value, str) else int(value)


def _float(value: Any) -> float:
    return float(value)


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


# -- conversion with analysis -------------------------------------------------


@dataclass
class ConvertedTemplate:
    """A converted template, loaded as a flow and analyzed."""

    source: TemplateSource
    document: dict[str, Any]
    flow: Flow
    findings: list[Finding]

    def counts(self) -> dict[str, int]:
        """How many components of each kind the definition holds, all groups included."""
        counts = dict.fromkeys(COUNT_KEYS, 0)

        def visit(group: dict[str, Any]) -> None:
            for key in COUNT_KEYS:
                counts[key] += len(group.get(key) or [])
            for child in group.get("processGroups") or []:
                visit(child)

        visit(self.document["flowContents"])
        return counts


COUNT_KEYS = (
    "processors",
    "controllerServices",
    "connections",
    "inputPorts",
    "outputPorts",
    "funnels",
    "labels",
    "remoteProcessGroups",
    "processGroups",
)


def convert_and_analyze(source: TemplateSource, catalog: Catalog) -> ConvertedTemplate:
    document = convert_template(source)
    flow = load_definition_document(document)
    flow.source_name = source.name
    return ConvertedTemplate(
        source=source, document=document, flow=flow, findings=analyze(flow, catalog)
    )


def slugify(name: str) -> str:
    """File-name-safe form of a template name: ``Removed Components`` -> ``removed-components``."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "template"
