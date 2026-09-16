"""Run every migration on one flow, in the order that keeps each step correct.

1. Variables to parameter contexts: needs the Expression Language scopes of
   the original 1.x component types, so it runs before any type changes.
2. Component replacements and the event-driven scheduling fix.
3. Templates to flow definitions: read from the migrated flow (templates
   themselves are untouched by the steps above) and written separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from flowport.catalog import Catalog
from flowport.loaders.templates import templates_in_flow
from flowport.model import Flow, InputKind
from flowport.rules import Finding
from flowport.transforms import Change, MigrationResult
from flowport.transforms.components import migrate_components
from flowport.transforms.templates import ConvertedTemplate, convert_and_analyze
from flowport.transforms.variables import migrate_variables


@dataclass
class PipelineResult:
    flow_result: MigrationResult
    templates: list[ConvertedTemplate] = field(default_factory=list)


def migrate_all(flow: Flow, catalog: Catalog, *, keep_unused: bool = False) -> PipelineResult:
    """Variables, then components, then templates; the input is not modified."""
    variables = migrate_variables(flow, catalog, keep_unused=keep_unused)
    components = migrate_components(variables.flow, catalog)
    combined = MigrationResult(
        flow=components.flow,
        changes=sorted([*variables.changes, *components.changes], key=Change.sort_key),
        findings=sorted([*variables.findings, *components.findings], key=Finding.sort_key),
    )
    templates: list[ConvertedTemplate] = []
    if flow.kind is InputKind.FLOW:
        templates = [
            convert_and_analyze(source, catalog) for source in templates_in_flow(combined.flow.raw)
        ]
    return PipelineResult(flow_result=combined, templates=templates)
