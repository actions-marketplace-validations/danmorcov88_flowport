"""Variable Registry rules: definitions, references and their EL scope."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from flowport.model import Component, ProcessGroup
from flowport.rules import Context, Finding, Rule, component_location, group_location
from flowport.rules.expression import find_references

PARAMETER_NAME = re.compile(r"^[A-Za-z0-9 ._-]+$")
SCOPE_NONE = "NONE"
SCOPE_ATTRIBUTES = "FLOWFILE_ATTRIBUTES"


@dataclass(frozen=True)
class Definition:
    group: ProcessGroup
    name: str
    value: str


class _Scope:
    """Variables visible in a group: its own plus its ancestors', nearest wins."""

    def __init__(self, parent: _Scope | None, group: ProcessGroup) -> None:
        self.definitions: dict[str, Definition] = dict(parent.definitions) if parent else {}
        for name, value in group.variables.items():
            self.definitions[name] = Definition(group=group, name=name, value=value)


class VariableRules(Rule):
    rule_ids = (
        "NIFI2-VARIABLES-DEFINED",
        "NIFI2-VARIABLE-REFERENCE",
        "NIFI2-VARIABLE-REFERENCE-ATTRIBUTE-SCOPE",
        "NIFI2-VARIABLE-REFERENCE-FUNCTION",
        "NIFI2-VARIABLE-REFERENCE-NO-EL",
        "NIFI2-VARIABLE-REFERENCE-UNKNOWN-SCOPE",
        "NIFI2-VARIABLE-UNUSED",
        "NIFI2-VARIABLE-NAME",
    )

    def run(self, ctx: Context) -> Iterator[Finding]:
        used: set[tuple[str, str]] = set()
        reference_findings: list[Finding] = []
        definitions: list[Definition] = []

        def visit(group: ProcessGroup, parent_scope: _Scope | None) -> None:
            scope = _Scope(parent_scope, group)
            for name, value in group.variables.items():
                definitions.append(Definition(group=group, name=name, value=value))
            for component in [*group.processors, *group.controller_services]:
                for finding, definition in self._component_references(ctx, component, scope):
                    reference_findings.append(finding)
                    used.add((definition.group.id, definition.name))
            for child in group.groups:
                visit(child, scope)

        visit(ctx.flow.root, None)

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
        yield from reference_findings

    @staticmethod
    def _component_references(
        ctx: Context, component: Component, scope: _Scope
    ) -> Iterator[tuple[Finding, Definition]]:
        if not scope.definitions:
            return
        for property_name, value in component.properties.items():
            if not value or "${" not in value:
                continue
            seen: set[str] = set()
            for ref in find_references(value):
                definition = scope.definitions.get(ref.name)
                if definition is None or ref.name in seen:
                    continue
                seen.add(ref.name)
                el_scope = ctx.catalog.property_scope(component.type, property_name)
                if el_scope is None:
                    rule_id = "NIFI2-VARIABLE-REFERENCE-UNKNOWN-SCOPE"
                elif el_scope == SCOPE_NONE:
                    rule_id = "NIFI2-VARIABLE-REFERENCE-NO-EL"
                elif ref.has_function:
                    rule_id = "NIFI2-VARIABLE-REFERENCE-FUNCTION"
                elif el_scope == SCOPE_ATTRIBUTES:
                    rule_id = "NIFI2-VARIABLE-REFERENCE-ATTRIBUTE-SCOPE"
                else:
                    rule_id = "NIFI2-VARIABLE-REFERENCE"
                finding = ctx.finding(
                    rule_id,
                    component_location(component),
                    property=property_name,
                    variable=ref.name,
                    defined_in=definition.group.path,
                    value=value,
                )
                yield finding, definition
