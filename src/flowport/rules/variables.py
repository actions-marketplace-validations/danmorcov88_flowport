"""Variable Registry rules: definitions, references and their EL scope.

The classification of every ``${name}`` occurrence (``find_variable_uses``) is
shared with ``flowport.transforms.variables``: the rule reports it, the
transform rewrites exactly the occurrences classified as auto-fixable.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from flowport.catalog import Catalog
from flowport.model import Component, Flow, ProcessGroup
from flowport.rules import Context, Finding, Rule, component_location, group_location
from flowport.rules.expression import Reference, find_references

PARAMETER_NAME = re.compile(r"^[A-Za-z0-9 ._-]+$")
SCOPE_NONE = "NONE"
SCOPE_ATTRIBUTES = "FLOWFILE_ATTRIBUTES"

RULE_REFERENCE = "NIFI2-VARIABLE-REFERENCE"
RULE_ATTRIBUTE_SCOPE = "NIFI2-VARIABLE-REFERENCE-ATTRIBUTE-SCOPE"
RULE_FUNCTION = "NIFI2-VARIABLE-REFERENCE-FUNCTION"
RULE_NO_EL = "NIFI2-VARIABLE-REFERENCE-NO-EL"
RULE_UNKNOWN_SCOPE = "NIFI2-VARIABLE-REFERENCE-UNKNOWN-SCOPE"
RULE_SENSITIVE = "NIFI2-VARIABLE-REFERENCE-SENSITIVE"


@dataclass(frozen=True)
class Definition:
    group: ProcessGroup
    name: str
    value: str


@dataclass(frozen=True)
class Use:
    """One ``${name}`` occurrence that resolves to a process group variable."""

    component: Component
    property_name: str
    value: str
    reference: Reference
    definition: Definition
    rule_id: str

    @property
    def evaluated(self) -> bool:
        """False when the property never evaluates Expression Language."""
        return self.rule_id != RULE_NO_EL


class _Scope:
    """Variables visible in a group: its own plus its ancestors', nearest wins."""

    def __init__(self, parent: _Scope | None, group: ProcessGroup) -> None:
        self.definitions: dict[str, Definition] = dict(parent.definitions) if parent else {}
        for name, value in group.variables.items():
            self.definitions[name] = Definition(group=group, name=name, value=value)


def find_variable_uses(flow: Flow, catalog: Catalog) -> list[Use]:
    """Every variable reference in the flow, classified, in document order."""
    uses: list[Use] = []

    def visit(group: ProcessGroup, parent_scope: _Scope | None) -> None:
        scope = _Scope(parent_scope, group)
        if scope.definitions:
            for component in [*group.processors, *group.controller_services]:
                uses.extend(_component_uses(catalog, component, scope))
        for child in group.groups:
            visit(child, scope)

    visit(flow.root, None)
    return uses


def _component_uses(catalog: Catalog, component: Component, scope: _Scope) -> Iterator[Use]:
    for property_name, value in component.properties.items():
        if not value or "${" not in value:
            continue
        for ref in find_references(value):
            definition = scope.definitions.get(ref.name)
            if definition is None:
                continue
            rule_id = _classify(catalog, component, property_name, ref)
            yield Use(
                component=component,
                property_name=property_name,
                value=value,
                reference=ref,
                definition=definition,
                rule_id=rule_id,
            )


def _classify(catalog: Catalog, component: Component, property_name: str, ref: Reference) -> str:
    el_scope = catalog.property_scope(component.type, property_name)
    if el_scope is None:
        return RULE_UNKNOWN_SCOPE
    if el_scope == SCOPE_NONE:
        return RULE_NO_EL
    if _is_sensitive(component, property_name):
        return RULE_SENSITIVE
    if ref.has_function:
        return RULE_FUNCTION
    if el_scope == SCOPE_ATTRIBUTES:
        return RULE_ATTRIBUTE_SCOPE
    return RULE_REFERENCE


def _is_sensitive(component: Component, property_name: str) -> bool:
    descriptors = component.raw.get("propertyDescriptors") or {}
    descriptor = descriptors.get(property_name) or {}
    return bool(descriptor.get("sensitive", False))


class VariableRules(Rule):
    rule_ids = (
        "NIFI2-VARIABLES-DEFINED",
        RULE_REFERENCE,
        RULE_ATTRIBUTE_SCOPE,
        RULE_FUNCTION,
        RULE_NO_EL,
        RULE_UNKNOWN_SCOPE,
        RULE_SENSITIVE,
        "NIFI2-VARIABLE-UNUSED",
        "NIFI2-VARIABLE-NAME",
    )

    def run(self, ctx: Context) -> Iterator[Finding]:
        uses = find_variable_uses(ctx.flow, ctx.catalog)
        used = {(use.definition.group.id, use.definition.name) for use in uses}

        for group in ctx.flow.groups():
            if not group.variables:
                continue
            names = sorted(group.variables)
            yield ctx.finding(
                "NIFI2-VARIABLES-DEFINED",
                group_location(group),
                count=len(names),
                names=", ".join(names),
            )
            for name in names:
                if not PARAMETER_NAME.match(name):
                    yield ctx.finding("NIFI2-VARIABLE-NAME", group_location(group), variable=name)
                if (group.id, name) not in used:
                    yield ctx.finding(
                        "NIFI2-VARIABLE-UNUSED",
                        group_location(group),
                        variable=name,
                        value=group.variables[name],
                    )

        # One finding per property, variable and classification.
        seen: set[tuple[str, str, str, str]] = set()
        for use in uses:
            key = (use.component.id, use.property_name, use.reference.name, use.rule_id)
            if key in seen:
                continue
            seen.add(key)
            yield ctx.finding(
                use.rule_id,
                component_location(use.component),
                property=use.property_name,
                variable=use.reference.name,
                defined_in=use.definition.group.path,
                value=use.value,
            )
