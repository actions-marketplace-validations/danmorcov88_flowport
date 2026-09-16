"""Normalized flow model.

The model is a thin, typed view over the JSON that NiFi writes. Every node
keeps its original JSON object in ``raw`` so that writers can round-trip
fields the model does not know about. Rules read the typed fields; transforms
edit ``raw`` and rebuild the model.
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InputKind(StrEnum):
    """What kind of file was loaded."""

    FLOW = "flow"  # flow.json / flow.json.gz (whole canvas)
    DEFINITION = "definition"  # exported process group / registry snapshot
    TEMPLATE = "template"  # XML template
    FLOW_XML = "flow-xml"  # flow.xml / flow.xml.gz (not supported)


class ComponentKind(StrEnum):
    PROCESSOR = "PROCESSOR"
    CONTROLLER_SERVICE = "CONTROLLER_SERVICE"
    REPORTING_TASK = "REPORTING_TASK"
    PARAMETER_PROVIDER = "PARAMETER_PROVIDER"
    FLOW_ANALYSIS_RULE = "FLOW_ANALYSIS_RULE"
    FLOW_REGISTRY_CLIENT = "FLOW_REGISTRY_CLIENT"
    PROCESS_GROUP = "PROCESS_GROUP"
    CONNECTION = "CONNECTION"
    INPUT_PORT = "INPUT_PORT"
    OUTPUT_PORT = "OUTPUT_PORT"
    TEMPLATE = "TEMPLATE"


class _Node(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    raw: dict[str, Any] = Field(repr=False, exclude=True)


class Bundle(BaseModel):
    group: str = ""
    artifact: str = ""
    version: str = ""

    @property
    def coordinate(self) -> str:
        return f"{self.group}:{self.artifact}:{self.version}"

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None) -> Bundle:
        raw = raw or {}
        return cls(
            group=str(raw.get("group", "")),
            artifact=str(raw.get("artifact", "")),
            version=str(raw.get("version", "")),
        )


class Component(_Node):
    """A processor, controller service, reporting task or similar extension instance."""

    kind: ComponentKind
    id: str
    versioned_id: str
    name: str
    type: str
    bundle: Bundle
    properties: dict[str, str | None]
    path: str
    group_id: str | None = None
    scheduling_strategy: str | None = None
    scheduling_period: str | None = None

    @property
    def short_type(self) -> str:
        return self.type.rsplit(".", 1)[-1]

    @classmethod
    def from_raw(cls, kind: ComponentKind, raw: dict[str, Any], path: str) -> Component:
        properties_raw = raw.get("properties") or {}
        return cls(
            raw=raw,
            kind=kind,
            id=_component_id(raw),
            versioned_id=str(raw.get("identifier", "")),
            name=str(raw.get("name", "")),
            type=str(raw.get("type", "")),
            bundle=Bundle.from_raw(raw.get("bundle")),
            properties={str(k): (None if v is None else str(v)) for k, v in properties_raw.items()},
            path=path,
            group_id=raw.get("groupIdentifier"),
            scheduling_strategy=raw.get("schedulingStrategy"),
            scheduling_period=raw.get("schedulingPeriod"),
        )


class Port(_Node):
    kind: ComponentKind
    id: str
    name: str
    path: str


class Connection(_Node):
    id: str
    name: str
    path: str
    source_id: str
    destination_id: str
    relationships: list[str]


class Template(_Node):
    id: str
    name: str
    path: str


class Parameter(BaseModel):
    name: str
    value: str | None = None
    sensitive: bool = False


class ParameterContext(_Node):
    id: str
    name: str
    parameters: list[Parameter]
    inherited: list[str]


class ProcessGroup(_Node):
    id: str
    versioned_id: str
    name: str
    path: str
    variables: dict[str, str]
    parameter_context_name: str | None = None
    processors: list[Component]
    controller_services: list[Component]
    input_ports: list[Port]
    output_ports: list[Port]
    connections: list[Connection]
    templates: list[Template]
    groups: list[ProcessGroup]

    def walk(self) -> Iterator[ProcessGroup]:
        """This group followed by all descendants, depth first, in document order."""
        yield self
        for child in self.groups:
            yield from child.walk()

    def all_components(self) -> Iterator[Component]:
        for group in self.walk():
            yield from group.processors
            yield from group.controller_services

    @classmethod
    def from_raw(cls, raw: dict[str, Any], parent_path: str) -> ProcessGroup:
        name = str(raw.get("name", ""))
        path = f"{parent_path}/{name}"
        group_id = _component_id(raw)
        variables_raw = raw.get("variables") or {}
        return cls(
            raw=raw,
            id=group_id,
            versioned_id=str(raw.get("identifier", "")),
            name=name,
            path=path,
            variables={str(k): str(v) for k, v in variables_raw.items()},
            parameter_context_name=raw.get("parameterContextName"),
            processors=[
                Component.from_raw(ComponentKind.PROCESSOR, p, path)
                for p in raw.get("processors") or []
            ],
            controller_services=[
                Component.from_raw(ComponentKind.CONTROLLER_SERVICE, s, path)
                for s in raw.get("controllerServices") or []
            ],
            input_ports=[
                Port(
                    raw=p,
                    kind=ComponentKind.INPUT_PORT,
                    id=_component_id(p),
                    name=str(p.get("name", "")),
                    path=path,
                )
                for p in raw.get("inputPorts") or []
            ],
            output_ports=[
                Port(
                    raw=p,
                    kind=ComponentKind.OUTPUT_PORT,
                    id=_component_id(p),
                    name=str(p.get("name", "")),
                    path=path,
                )
                for p in raw.get("outputPorts") or []
            ],
            connections=[
                Connection(
                    raw=c,
                    id=_component_id(c),
                    name=str(c.get("name", "")),
                    path=path,
                    source_id=str((c.get("source") or {}).get("id", "")),
                    destination_id=str((c.get("destination") or {}).get("id", "")),
                    relationships=[str(r) for r in c.get("selectedRelationships") or []],
                )
                for c in raw.get("connections") or []
            ],
            templates=[
                Template(raw=t, id=_component_id(t), name=str(t.get("name", "")), path=path)
                for t in raw.get("templates") or []
            ],
            groups=[ProcessGroup.from_raw(g, path) for g in raw.get("processGroups") or []],
        )


class Flow(_Node):
    """A loaded flow: the root process group plus controller-level objects."""

    kind: InputKind
    root: ProcessGroup
    parameter_contexts: list[ParameterContext]
    controller_services: list[Component]
    reporting_tasks: list[Component]
    templates: list[Template]
    source_name: str = ""

    def groups(self) -> Iterator[ProcessGroup]:
        return self.root.walk()

    def components(self) -> Iterator[Component]:
        """Every extension instance in the flow, in document order."""
        yield from self.controller_services
        yield from self.reporting_tasks
        yield from self.root.all_components()

    def all_templates(self) -> Iterator[Template]:
        yield from self.templates
        for group in self.groups():
            yield from group.templates

    @property
    def bundle_versions(self) -> dict[str, int]:
        """Count of components per Apache NiFi bundle version; hints at the source release."""
        counts: dict[str, int] = {}
        for component in self.components():
            if component.bundle.group == "org.apache.nifi" and component.bundle.version:
                counts[component.bundle.version] = counts.get(component.bundle.version, 0) + 1
        return counts


def _component_id(raw: dict[str, Any]) -> str:
    """The id a user sees in the NiFi UI, falling back to the versioned id."""
    return str(raw.get("instanceIdentifier") or raw.get("identifier") or raw.get("id") or "")


def parameter_contexts_from_raw(raw: Any) -> list[ParameterContext]:
    """Accepts the list form (flow.json) and the name-keyed dict form (flow definition)."""
    if isinstance(raw, dict):
        entries = [dict(v, name=v.get("name", k)) for k, v in raw.items() if isinstance(v, dict)]
    elif isinstance(raw, list):
        entries = [e for e in raw if isinstance(e, dict)]
    else:
        entries = []
    contexts: list[ParameterContext] = []
    for entry in entries:
        params_raw = entry.get("parameters") or []
        parameters = [
            Parameter(
                name=str(p.get("name", "")),
                value=None if p.get("value") is None else str(p["value"]),
                sensitive=bool(p.get("sensitive", False)),
            )
            for p in params_raw
            if isinstance(p, dict)
        ]
        contexts.append(
            ParameterContext(
                raw=entry,
                id=_component_id(entry),
                name=str(entry.get("name", "")),
                parameters=parameters,
                inherited=[str(i) for i in entry.get("inheritedParameterContexts") or []],
            )
        )
    return contexts
