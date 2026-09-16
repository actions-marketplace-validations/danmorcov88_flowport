"""Transforms edit the raw document of a loaded flow and record every change.

A transform never touches the input file: it works on a deep copy of the
document, rebuilds the model from the edited copy and returns both, together
with the change log and the findings it could not fix.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from flowport.model import Flow
from flowport.rules import Finding

CHANGE_KINDS = (
    "create-context",
    "add-parameter",
    "assign-context",
    "rewrite-property",
    "remove-variable",
)


class Change(BaseModel):
    """One edit made to the flow.

    ``path`` is the process group path; ``component_id``/``component_name``
    identify the process group or component that was edited. ``property``
    holds the property name for rewrites and the parameter or variable name
    for parameter and variable changes. ``context`` is the parameter context
    involved.
    """

    kind: str
    path: str
    component_id: str
    component_name: str
    property: str | None = None
    old: str | None = None
    new: str | None = None
    context: str | None = None

    def sort_key(self) -> tuple[Any, ...]:
        return (
            self.path,
            self.component_name,
            self.component_id,
            CHANGE_KINDS.index(self.kind),
            self.property or "",
            self.context or "",
            self.old or "",
            self.new or "",
        )


@dataclass
class MigrationResult:
    flow: Flow
    changes: list[Change] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {kind: sum(1 for c in self.changes if c.kind == kind) for kind in CHANGE_KINDS}


def render_changes(changes: list[Change]) -> str:
    """The ``changes.json`` document: a list of changes in a stable order."""
    entries = [c.model_dump() for c in sorted(changes, key=Change.sort_key)]
    return json.dumps(entries, indent=2, ensure_ascii=False) + "\n"
