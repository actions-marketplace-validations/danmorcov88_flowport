"""Replace components with their documented successors and fix removed scheduling.

Only the replacements listed in ``catalog/replacements.yaml`` are applied,
each one property by property and relationship by relationship as the YAML
says. A component keeps its identifier, name, comments, position and
connections; connections and auto-terminated relationships are remapped
through the ``relationships`` table of the replacement. A replacement whose
``unless`` condition holds is not applied and reported instead.

Processors scheduled EVENT_DRIVEN, a strategy NiFi 2.x removed
(NIFI-11813; the deprecation table names timer-driven as the replacement),
are switched to TIMER_DRIVEN with a run schedule of 0 sec.
"""

from __future__ import annotations

import copy
from typing import Any

from flowport.catalog import Catalog, Replacement, TypeProperties
from flowport.loaders import load_definition_document, load_flow_document
from flowport.model import Component, ComponentKind, Flow, InputKind, ProcessGroup
from flowport.rules import Context, Finding, component_location
from flowport.transforms import Change, MigrationResult

EVENT_DRIVEN = "EVENT_DRIVEN"
TIMER_DRIVEN = "TIMER_DRIVEN"
TIMER_DRIVEN_PERIOD = "0 sec"


def migrate_components(flow: Flow, catalog: Catalog) -> MigrationResult:
    """Return a migrated copy of ``flow``; the input is not modified."""
    document = copy.deepcopy(flow.raw)
    work = _reload(document, flow.kind, flow.source_name)
    migration = _Migration(work, catalog)
    migration.run()
    return MigrationResult(
        flow=_reload(document, flow.kind, flow.source_name),
        changes=sorted(migration.changes, key=Change.sort_key),
        findings=sorted(migration.findings, key=Finding.sort_key),
    )


def _reload(document: dict[str, Any], kind: InputKind, source_name: str) -> Flow:
    flow = (
        load_flow_document(document)
        if kind is InputKind.FLOW
        else load_definition_document(document)
    )
    flow.source_name = source_name
    return flow


class _Migration:
    def __init__(self, flow: Flow, catalog: Catalog) -> None:
        self.flow = flow
        self.catalog = catalog
        self.ctx = Context(flow, catalog)
        self.changes: list[Change] = []
        self.findings: list[Finding] = []
        self.owner: dict[str, ProcessGroup] = {}
        for group in flow.groups():
            for component in [*group.processors, *group.controller_services]:
                self.owner[component.id] = group

    def run(self) -> None:
        for component in self.flow.components():
            replacement = self.catalog.replacements.get(component.type)
            if replacement is not None and replacement.kind == component.kind.value:
                self._replace(component, replacement)
            if component.kind is ComponentKind.PROCESSOR:
                self._fix_scheduling(component)

    # -- replacements ---------------------------------------------------------

    def _replace(self, component: Component, replacement: Replacement) -> None:
        for condition in replacement.unless:
            value = component.properties.get(condition.property)
            if value is not None and value.strip().lower() == condition.equals.lower():
                self.findings.append(
                    self.ctx.finding(
                        "NIFI2-REPLACEMENT-SKIPPED",
                        component_location(component),
                        to_type=replacement.to_type,
                        property=condition.property,
                        value=value,
                        reason=condition.reason,
                    )
                )
                return

        raw = component.raw
        target = self.catalog.target_properties.get(replacement.to_type)
        old_type = component.type
        raw["type"] = replacement.to_type
        raw["bundle"] = {
            "group": replacement.bundle_group,
            "artifact": replacement.bundle_artifact,
            "version": self.catalog.target_version,
        }
        self._change(component, "replace-component", old=old_type, new=replacement.to_type)
        self._map_properties(component, replacement, target)
        if component.kind is ComponentKind.PROCESSOR:
            self._map_relationships(component, replacement)

        # The finding describes the component as it is now (new type), so the
        # location is rebuilt from the edited raw JSON.
        location = component_location(Component.from_raw(component.kind, raw, component.path))
        self.findings.append(
            self.ctx.finding(
                "NIFI2-COMPONENT-REPLACED",
                location,
                from_type=old_type,
                from_short=old_type.rsplit(".", 1)[-1],
                to_type=replacement.to_type,
                note=replacement.note,
            )
        )

    def _map_properties(
        self, component: Component, replacement: Replacement, target: TypeProperties | None
    ) -> None:
        raw = component.raw
        old_properties: dict[str, Any] = raw.get("properties") or {}
        old_descriptors: dict[str, Any] = raw.get("propertyDescriptors") or {}
        source = self.catalog.properties.get(component.type)
        declared = set(source.properties) if source else set(old_properties)
        new_properties: dict[str, Any] = {}
        new_descriptors: dict[str, Any] = {}

        def descriptor(old_name: str, new_name: str) -> dict[str, Any]:
            entry = dict(old_descriptors.get(old_name) or {})
            entry["name"] = new_name
            if "displayName" in entry and entry["displayName"] == old_name:
                entry["displayName"] = new_name
            entry.setdefault("identifiesControllerService", False)
            entry.setdefault("sensitive", False)
            return entry

        for name, value in old_properties.items():
            if name in replacement.properties:
                new_name = replacement.properties[name]
                new_value = self._mapped_value(replacement, new_name, value)
                new_properties[new_name] = new_value
                new_descriptors[new_name] = descriptor(name, new_name)
                self._change(
                    component,
                    "rename-property",
                    prop=f"{name} -> {new_name}",
                    old=value,
                    new=new_value,
                )
            elif name in replacement.drop:
                self._drop(component, replacement, name, value)
            elif target is not None and name in target.properties:
                new_properties[name] = self._mapped_value(replacement, name, value)
                new_descriptors[name] = descriptor(name, name)
            elif name not in declared and (target is None or target.dynamic is not None):
                new_properties[name] = value  # dynamic property, kept as is
                new_descriptors[name] = descriptor(name, name)
            else:
                self._drop(component, replacement, name, value)

        for name, value in replacement.set.items():
            old = new_properties.get(name)
            new_properties[name] = value
            new_descriptors.setdefault(
                name,
                {
                    "name": name,
                    "displayName": name,
                    "identifiesControllerService": False,
                    "sensitive": False,
                },
            )
            if old != value:
                self._change(component, "set-property", prop=name, old=old, new=value)

        raw["properties"] = new_properties
        if "propertyDescriptors" in raw or new_descriptors:
            raw["propertyDescriptors"] = new_descriptors

    @staticmethod
    def _mapped_value(replacement: Replacement, new_name: str, value: Any) -> Any:
        mapping = replacement.values.get(new_name)
        if value is None or mapping is None:
            return value
        return mapping.get(str(value).strip(), value)

    def _drop(self, component: Component, replacement: Replacement, name: str, value: Any) -> None:
        self._change(component, "drop-property", prop=name, old=value)
        default = replacement.drop.get(name)
        if value is not None and str(value).strip() != (default or ""):
            self.findings.append(
                self.ctx.finding(
                    "NIFI2-REPLACED-PROPERTY-DROPPED",
                    component_location(
                        Component.from_raw(component.kind, component.raw, component.path)
                    ),
                    from_short=component.short_type,
                    property=name,
                    value=str(value),
                )
            )

    def _map_relationships(self, component: Component, replacement: Replacement) -> None:
        raw = component.raw
        mapping = replacement.relationships
        old_terminated = [str(r) for r in raw.get("autoTerminatedRelationships") or []]
        terminated = _remap(old_terminated, mapping)
        for name in replacement.terminate:
            if name not in terminated:
                terminated.append(name)
        if terminated != old_terminated:
            raw["autoTerminatedRelationships"] = terminated
            self._change(
                component,
                "terminate-relationship",
                old=", ".join(old_terminated),
                new=", ".join(terminated),
            )
        if not mapping:
            return
        group = self.owner.get(component.id)
        for connection in group.connections if group else []:
            source = connection.raw.get("source") or {}
            if (
                source.get("id") != component.versioned_id
                and (source.get("instanceIdentifier") or source.get("id")) != component.id
            ):
                continue
            old_selected = [str(r) for r in connection.raw.get("selectedRelationships") or []]
            selected = _remap(old_selected, mapping)
            if selected != old_selected:
                connection.raw["selectedRelationships"] = selected
                destination = (connection.raw.get("destination") or {}).get("name") or "?"
                self.changes.append(
                    Change(
                        kind="map-relationship",
                        path=connection.path,
                        component_id=connection.id,
                        component_name=connection.name or f"{component.name} -> {destination}",
                        property="selectedRelationships",
                        old=", ".join(old_selected),
                        new=", ".join(selected),
                        context=component.name,
                    )
                )

    # -- scheduling -------------------------------------------------------------

    def _fix_scheduling(self, component: Component) -> None:
        raw = component.raw
        if raw.get("schedulingStrategy") != EVENT_DRIVEN:
            return
        old_period = raw.get("schedulingPeriod")
        raw["schedulingStrategy"] = TIMER_DRIVEN
        raw["schedulingPeriod"] = TIMER_DRIVEN_PERIOD
        self._change(
            component,
            "set-scheduling",
            prop="schedulingStrategy",
            old=f"{EVENT_DRIVEN} ({old_period})",
            new=f"{TIMER_DRIVEN} ({TIMER_DRIVEN_PERIOD})",
        )
        self.findings.append(
            self.ctx.finding(
                "NIFI2-SCHEDULING-CHANGED",
                component_location(Component.from_raw(component.kind, raw, component.path)),
            )
        )

    # -- helpers ----------------------------------------------------------------

    def _change(
        self,
        component: Component,
        kind: str,
        *,
        prop: str | None = None,
        old: Any = None,
        new: Any = None,
    ) -> None:
        self.changes.append(
            Change(
                kind=kind,
                path=component.path,
                component_id=component.id,
                component_name=component.name,
                property=prop,
                old=None if old is None else str(old),
                new=None if new is None else str(new),
            )
        )


def _remap(relationships: list[str], mapping: dict[str, tuple[str, ...]]) -> list[str]:
    result: list[str] = []
    for name in relationships:
        for new in mapping.get(name, (name,)):
            if new not in result:
                result.append(new)
    return result
