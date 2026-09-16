"""Build a report document from findings and render it in several formats.

The report dict is the single source for every format. JSON output is
deterministic: no timestamps, sorted keys, stable finding order.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from flowport import __version__
from flowport.catalog import Catalog
from flowport.model import Flow
from flowport.rules import Finding, Severity

SEVERITY_ORDER = (Severity.BLOCKER, Severity.MANUAL, Severity.AUTO_FIXABLE, Severity.INFO)


def build_report(
    flow: Flow, findings: list[Finding], catalog: Catalog, input_path: Path | None
) -> dict[str, Any]:
    by_severity = Counter(f.severity.value for f in findings)
    by_category = Counter(f.category for f in findings)
    by_rule = Counter(f.rule_id for f in findings)
    component_count = sum(1 for _ in flow.components())
    group_count = sum(1 for _ in flow.groups())
    input_info: dict[str, Any] = {"name": flow.source_name, "kind": flow.kind.value}
    if input_path is not None:
        input_info["sha256"] = hashlib.sha256(input_path.read_bytes()).hexdigest()
    return {
        "tool": {"name": "flowport", "version": __version__},
        "catalog": {
            "source_version": catalog.source_version,
            "target_version": catalog.target_version,
        },
        "input": input_info,
        "summary": {
            "total": len(findings),
            "by_severity": {s.value: by_severity.get(s.value, 0) for s in SEVERITY_ORDER},
            "by_category": dict(sorted(by_category.items())),
            "by_rule": dict(sorted(by_rule.items())),
            "process_groups": group_count,
            "components": component_count,
            "bundle_versions": dict(sorted(flow.bundle_versions.items())),
        },
        "findings": [f.model_dump(mode="json") for f in findings],
    }


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render(report: dict[str, Any], fmt: str) -> str:
    if fmt == "json":
        return render_json(report)
    if fmt == "markdown":
        from flowport.reports.markdown import render_markdown

        return render_markdown(report)
    if fmt == "html":
        from flowport.reports.html import render_html

        return render_html(report)
    raise ValueError(f"unknown report format {fmt!r}")
