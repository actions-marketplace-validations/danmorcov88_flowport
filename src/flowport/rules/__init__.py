"""Rule engine: run every rule over a flow and return sorted findings."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from flowport.catalog import Catalog
from flowport.model import Component, ComponentKind, Flow, ProcessGroup, Template


class Severity(StrEnum):
    BLOCKER = "BLOCKER"
    MANUAL = "MANUAL"
    AUTO_FIXABLE = "AUTO_FIXABLE"
    INFO = "INFO"

    @property
    def rank(self) -> int:
        return _RANK[self]


_RANK = {
    Severity.INFO: 0,
    Severity.AUTO_FIXABLE: 1,
    Severity.MANUAL: 2,
    Severity.BLOCKER: 3,
}


class Location(BaseModel):
    """Where a finding points to. `path` is the process group path."""

    id: str
    name: str
    type: str
    kind: str
    path: str


class Finding(BaseModel):
    rule_id: str
    severity: Severity
    category: str
    title: str
    location: Location
    message: str
    suggestion: str
    sources: list[str]
    details: dict[str, str] = {}

    def sort_key(self) -> tuple[Any, ...]:
        return (
            self.location.path,
            self.location.name,
            self.location.id,
            self.rule_id,
            tuple(sorted(self.details.items())),
            self.message,
        )


class Context:
    """What rules get: the flow, the catalog, and a way to build findings."""

    def __init__(self, flow: Flow, catalog: Catalog) -> None:
        self.flow = flow
        self.catalog = catalog
        self._groups_by_id: dict[str, ProcessGroup] = {g.id: g for g in flow.groups()}

    def group(self, group_id: str) -> ProcessGroup | None:
        return self._groups_by_id.get(group_id)

    def finding(
        self,
        rule_id: str,
        location: Location,
        *,
        extra_sources: Iterable[str] = (),
        **fields: Any,
    ) -> Finding:
        spec = self.catalog.rule(rule_id)
        fields.setdefault("short_type", location.type.rsplit(".", 1)[-1])
        fields.setdefault("type", location.type)
        fields.setdefault("source_version", self.catalog.source_version)
        fields.setdefault("target_version", self.catalog.target_version)
        sources = list(spec.sources)
        for ref in extra_sources:
            url = self.catalog.resolve_source(ref)
            if url not in sources:
                sources.append(url)
        details = {
            k: str(v)
            for k, v in fields.items()
            if k not in {"short_type", "type", "source_version", "target_version"}
        }
        return Finding(
            rule_id=rule_id,
            severity=Severity(spec.severity),
            category=spec.category,
            title=spec.title,
            location=location,
            message=spec.message.format(**fields),
            suggestion=spec.suggestion.format(**fields),
            sources=sources,
            details=details,
        )


def component_location(component: Component) -> Location:
    return Location(
        id=component.id,
        name=component.name,
        type=component.type,
        kind=component.kind.value,
        path=component.path,
    )


def group_location(group: ProcessGroup) -> Location:
    return Location(
        id=group.id,
        name=group.name,
        type="",
        kind=ComponentKind.PROCESS_GROUP.value,
        path=group.path,
    )


def template_location(template: Template, path: str) -> Location:
    return Location(
        id=template.id,
        name=template.name,
        type="",
        kind=ComponentKind.TEMPLATE.value,
        path=path,
    )


class Rule:
    """Base class. Subclasses set `rule_ids` (for documentation) and implement run()."""

    rule_ids: tuple[str, ...] = ()

    def run(self, ctx: Context) -> Iterator[Finding]:
        raise NotImplementedError


def all_rules() -> list[Rule]:
    from flowport.rules import components, features, variables

    return [
        components.ComponentInventoryRule(),
        components.ScriptEngineRule(),
        variables.VariableRules(),
        features.EventDrivenRule(),
        features.CronRule(),
        features.TemplateRule(),
        features.InvokeHttpProxyRule(),
    ]


def analyze(flow: Flow, catalog: Catalog, rules: Iterable[Rule] | None = None) -> list[Finding]:
    """Run the rules and return findings in a stable order."""
    ctx = Context(flow, catalog)
    findings: list[Finding] = []
    for rule in rules if rules is not None else all_rules():
        findings.extend(rule.run(ctx))
    findings.sort(key=Finding.sort_key)
    return findings
