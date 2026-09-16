"""Markdown report."""

from __future__ import annotations

from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from flowport.reports import SEVERITY_ORDER


def environment() -> Environment:
    return Environment(
        loader=PackageLoader("flowport.reports", "templates"),
        autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=False),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def grouped(report: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    findings = report["findings"]
    return [
        (severity.value, [f for f in findings if f["severity"] == severity.value])
        for severity in SEVERITY_ORDER
    ]


def render_markdown(report: dict[str, Any]) -> str:
    template = environment().get_template("report.md.j2")
    return template.render(report=report, grouped=grouped(report))
