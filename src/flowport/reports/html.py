"""Single-file HTML report with inline styles, no external assets."""

from __future__ import annotations

from typing import Any

from flowport.reports.markdown import environment, grouped


def render_html(report: dict[str, Any]) -> str:
    template = environment().get_template("report.html.j2")
    return template.render(report=report, grouped=grouped(report))
