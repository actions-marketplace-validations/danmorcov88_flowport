"""Convert process group variables to parameter contexts.

What the migration does, in order:

1. Walk the process groups top-down. A group that defines variables gets a
   parameter context: its existing one when it has one and no name collides,
   otherwise a new context named ``<group name> Variables`` that inherits from
   the nearest ancestor context, so that variables inherited on 1.x keep
   resolving through parameter context inheritance (NIFI-8490).
2. Rewrite ``${name}`` to ``#{name}`` for the references the analyzer marks as
   ``NIFI2-VARIABLE-REFERENCE`` (property evaluates the Variable Registry
   only, no functions), and only when ``#{name}`` resolves to the parameter
   created for that variable through the group's context. Groups without a
   context of their own get the nearest ancestor context assigned, because a
   process group does not inherit its parent's context.
3. Remove a variable from the group once every evaluated reference to it was
   rewritten. Variables with references that need a human decision stay, so
   the output still works on NiFi 1.x.

Everything else stays a finding. Same input, same output: generated
identifiers derive from the source group id, and every list is ordered.
"""

from __future__ import annotations

import copy
import hashlib
import uuid
from collections import defaultdict
from typing import Any

from flowport.catalog import Catalog
from flowport.loaders import load_definition_document, load_flow_document
from flowport.model import Flow, InputKind, ProcessGroup
from flowport.rules import Context, Finding, component_location, group_location
from flowport.rules.expression import Reference
from flowport.rules.variables import (
    PARAMETER_NAME,
    RULE_REFERENCE,
    Use,
    find_variable_uses,
)
from flowport.transforms import Change, MigrationResult

# Fixed namespace for generated identifiers: uuid5(NAMESPACE_URL, project URL).
NAMESPACE = uuid.UUID("c0231dd3-8a1d-532c-8217-c4dcb4e6bc7f")
CONTEXT_SUFFIX = " Variables"


def migrate_variables(
    flow: Flow, catalog: Catalog, *, keep_unused: bool = False
) -> MigrationResult:
    """Return a migrated copy of ``flow``; the input is not modified."""
    document = copy.deepcopy(flow.raw)
    work = _reload(document, flow.kind, flow.source_name)
    migration = _Migration(work, document, catalog, keep_unused=keep_unused)
    migration.run()
    result = MigrationResult(
        flow=_reload(document, flow.kind, flow.source_name),
        changes=sorted(migration.changes, key=Change.sort_key),
        findings=sorted(migration.findings, key=Finding.sort_key),
    )
    return result


def _reload(document: dict[str, Any], kind: InputKind, source_name: str) -> Flow:
    if kind is InputKind.FLOW:
        flow = load_flow_document(document)
    else:
        flow = load_definition_document(document)
    flow.source_name = source_name
    return flow


def instance_identifier(group_id: str) -> str:
    """Instance id of the context created for a group."""
    return str(uuid.uuid5(NAMESPACE, f"parameter-context:{group_id}"))


def versioned_identifier(instance_id: str) -> str:
    """The id NiFi derives for versioned components: ``UUID.nameUUIDFromBytes(instanceId)``."""
    digest = bytearray(hashlib.md5(instance_id.encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


class _Contexts:
    """The parameter contexts of the document, existing and created, by name."""

    def __init__(self, document: dict[str, Any], kind: InputKind) -> None:
        self.document = document
        self.kind = kind
        self.entries: dict[str, dict[str, Any]] = {}
        raw = document.get("parameterContexts")
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, dict) and "name" in entry:
                    self.entries[str(entry["name"])] = entry
        elif isinstance(raw, dict):
            for name, entry in raw.items():
                if isinstance(entry, dict):
                    self.entries[str(entry.get("name", name))] = entry

    def __contains__(self, name: str) -> bool:
        return name in self.entries

    def own_parameters(self, name: str) -> set[str]:
        entry = self.entries.get(name) or {}
        return {str(p["name"]) for p in entry.get("parameters") or [] if isinstance(p, dict)}

    def inherited(self, name: str) -> list[str]:
        entry = self.entries.get(name) or {}
        return [str(n) for n in entry.get("inheritedParameterContexts") or []]

    def resolve(self, context: str, parameter: str) -> str | None:
        """Name of the context whose parameter ``#{parameter}`` resolves to.

        A context's own parameters win over inherited ones; inherited contexts
        are searched in order, depth first.
        """
        return self._resolve(context, parameter, set())

    def _resolve(self, context: str, parameter: str, seen: set[str]) -> str | None:
        if context in seen or context not in self.entries:
            return None
        seen.add(context)
        if parameter in self.own_parameters(context):
            return context
        for inherited in self.inherited(context):
            found = self._resolve(inherited, parameter, seen)
            if found is not None:
                return found
        return None

    def unique_name(self, base: str) -> str:
        name = base
        counter = 1
        while name in self.entries:
            counter += 1
            name = f"{base} {counter}"
        return name

    def create(self, name: str, group: ProcessGroup, inherits: str | None) -> None:
        description = f"Parameters converted from the variables of process group {group.path}"
        entry: dict[str, Any]
        if self.kind is InputKind.FLOW:
            instance_id = instance_identifier(group.id)
            entry = {
                "identifier": versioned_identifier(instance_id),
                "instanceIdentifier": instance_id,
                "name": name,
                "parameters": [],
                "inheritedParameterContexts": [inherits] if inherits else [],
                "description": description,
                "componentType": "PARAMETER_CONTEXT",
            }
            self.document.setdefault("parameterContexts", []).append(entry)
        else:
            entry = {
                "name": name,
                "parameters": [],
                "inheritedParameterContexts": [inherits] if inherits else [],
                "description": description,
                "componentType": "PARAMETER_CONTEXT",
            }
            contexts = self.document.get("parameterContexts")
            if not isinstance(contexts, dict):
                contexts = self.document["parameterContexts"] = {}
            contexts[name] = entry
        self.entries[name] = entry

    def add_parameter(self, context: str, name: str, value: str, group: ProcessGroup) -> None:
        parameters = self.entries[context].setdefault("parameters", [])
        parameters.append(
            {
                "name": name,
                "description": f"Converted from variable '{name}' of process group {group.path}",
                "sensitive": False,
                "provided": False,
                "value": value,
            }
        )


class _Migration:
    def __init__(
        self, flow: Flow, document: dict[str, Any], catalog: Catalog, *, keep_unused: bool
    ) -> None:
        self.flow = flow
        self.catalog = catalog
        self.keep_unused = keep_unused
        self.ctx = Context(flow, catalog)
        self.contexts = _Contexts(document, flow.kind)
        self.changes: list[Change] = []
        self.findings: list[Finding] = []
        self.uses = find_variable_uses(flow, catalog)
        self.uses_of: dict[tuple[str, str], list[Use]] = defaultdict(list)
        for use in self.uses:
            self.uses_of[(use.definition.group.id, use.definition.name)].append(use)
        self.parent: dict[str, ProcessGroup | None] = {}
        self.owner: dict[str, ProcessGroup] = {}
        for group in flow.groups():
            self.parent.setdefault(group.id, None)
            for child in group.groups:
                self.parent[child.id] = group
            for component in [*group.processors, *group.controller_services]:
                self.owner[component.id] = group
        # Context each group resolves parameters through after the migration
        # (None when there is none), and whether it is set on the group already.
        self.group_context: dict[str, str | None] = {}
        self.assigned: set[str] = set()
        # (group id, variable name) -> context holding the parameter created for it.
        self.parameters: dict[tuple[str, str], str] = {}
        # Variables whose parameter exists but that must stay because a
        # reference could not be rewritten.
        self.blocked: set[tuple[str, str]] = set()

    # -- phase 1: contexts and parameters -----------------------------------

    def run(self) -> None:
        for group in self.flow.groups():
            self._plan_group(group)
        self._rewrite_references()
        self._remove_migrated_variables()

    def _plan_group(self, group: ProcessGroup) -> None:
        parent = self.parent[group.id]
        parent_context = self.group_context[parent.id] if parent else None
        candidates = self._candidates(group)

        if group.parameter_context_name:
            context = group.parameter_context_name
            self.group_context[group.id] = context
            self.assigned.add(group.id)
            if not candidates or context not in self.contexts:
                return
            colliding = [n for n in candidates if self.contexts.resolve(context, n) is not None]
            if colliding:
                for name in colliding:
                    self.findings.append(
                        self.ctx.finding(
                            "NIFI2-VARIABLE-PARAMETER-COLLISION",
                            group_location(group),
                            variable=name,
                            context=context,
                            value=group.variables[name],
                        )
                    )
                return
            for name in candidates:
                self._add_parameter(context, group, name)
            return

        if not candidates:
            self.group_context[group.id] = parent_context
            return

        context = self.contexts.unique_name(f"{group.name}{CONTEXT_SUFFIX}")
        self.contexts.create(context, group, parent_context)
        self.changes.append(
            Change(
                kind="create-context",
                path=group.path,
                component_id=group.id,
                component_name=group.name,
                context=context,
                new=parent_context,
            )
        )
        for name in candidates:
            self._add_parameter(context, group, name)
        self.group_context[group.id] = context
        self._assign(group, context)

    def _candidates(self, group: ProcessGroup) -> list[str]:
        """Variables of the group that become parameters, sorted by name."""
        names = []
        for name in sorted(group.variables):
            if not PARAMETER_NAME.match(name):
                continue
            if self.keep_unused or any(u.evaluated for u in self.uses_of[(group.id, name)]):
                names.append(name)
        return names

    def _add_parameter(self, context: str, group: ProcessGroup, name: str) -> None:
        value = group.variables[name]
        self.contexts.add_parameter(context, name, value, group)
        self.parameters[(group.id, name)] = context
        self.changes.append(
            Change(
                kind="add-parameter",
                path=group.path,
                component_id=group.id,
                component_name=group.name,
                property=name,
                new=value,
                context=context,
            )
        )

    def _assign(self, group: ProcessGroup, context: str) -> None:
        group.raw["parameterContextName"] = context
        self.assigned.add(group.id)
        self.changes.append(
            Change(
                kind="assign-context",
                path=group.path,
                component_id=group.id,
                component_name=group.name,
                context=context,
            )
        )

    # -- phase 2: references --------------------------------------------------

    def _rewrite_references(self) -> None:
        pending: dict[tuple[str, str], list[Use]] = defaultdict(list)
        for use in self.uses:
            key = (use.definition.group.id, use.definition.name)
            holder = self.parameters.get(key)
            if holder is None or use.rule_id != RULE_REFERENCE:
                continue
            group = self.owner[use.component.id]
            context = self.group_context[group.id]
            resolved = self.contexts.resolve(context, use.reference.name) if context else None
            if context is None or resolved != holder:
                self.blocked.add(key)
                if context is not None:
                    self._not_visible(use, holder, context, resolved)
                continue
            if _span_end(use.value, use.reference) is None:
                self.blocked.add(key)  # malformed expression, leave it alone
                continue
            if group.id not in self.assigned:
                self._assign(group, context)
            pending[(use.component.id, use.property_name)].append(use)

        for uses in pending.values():
            component = uses[0].component
            property_name = uses[0].property_name
            old = uses[0].value
            new = rewrite_value(old, [u.reference for u in uses])
            component.raw["properties"][property_name] = new
            self.changes.append(
                Change(
                    kind="rewrite-property",
                    path=component.path,
                    component_id=component.id,
                    component_name=component.name,
                    property=property_name,
                    old=old,
                    new=new,
                    context=self.group_context[self.owner[component.id].id],
                )
            )

    def _not_visible(self, use: Use, holder: str, context: str, resolved: str | None) -> None:
        if resolved is None:
            reason = "does not inherit it"
        else:
            reason = f"already resolves #{{{use.reference.name}}} to a parameter of '{resolved}'"
        self.findings.append(
            self.ctx.finding(
                "NIFI2-VARIABLE-REFERENCE-NOT-VISIBLE",
                component_location(use.component),
                property=use.property_name,
                variable=use.reference.name,
                defined_in=use.definition.group.path,
                value=use.value,
                parameter_context=holder,
                context=context,
                reason=reason,
            )
        )

    # -- phase 3: variables ---------------------------------------------------

    def _remove_migrated_variables(self) -> None:
        for (group_id, name), context in self.parameters.items():
            if (group_id, name) in self.blocked:
                continue
            uses = self.uses_of[(group_id, name)]
            if any(u.evaluated and u.rule_id != RULE_REFERENCE for u in uses):
                continue
            group = self.ctx.group(group_id)
            assert group is not None
            value = group.raw["variables"].pop(name)
            self.changes.append(
                Change(
                    kind="remove-variable",
                    path=group.path,
                    component_id=group.id,
                    component_name=group.name,
                    property=name,
                    old=str(value),
                    context=context,
                )
            )


def _span_end(text: str, ref: Reference) -> int | None:
    """Index after the ``}`` that closes a plain ``${name}`` reference, or None."""
    pos = ref.subject_end + (1 if ref.quoted else 0)
    while pos < len(text) and text[pos] in " \t\r\n":
        pos += 1
    if pos < len(text) and text[pos] == "}":
        return pos + 1
    return None


def rewrite_value(value: str, references: list[Reference]) -> str:
    """Replace the given plain references in ``value`` with ``#{name}``."""
    for ref in sorted(references, key=lambda r: r.start, reverse=True):
        end = _span_end(value, ref)
        if end is None:
            continue
        value = f"{value[: ref.start]}#{{{ref.name}}}{value[end:]}"
    return value
