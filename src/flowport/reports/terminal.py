"""Rich terminal output: summary first, then details grouped by severity."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from flowport.reports import SEVERITY_ORDER

STYLES = {
    "BLOCKER": "bold red",
    "MANUAL": "bold yellow",
    "AUTO_FIXABLE": "bold cyan",
    "INFO": "dim",
}


def _location(finding: dict[str, Any]) -> str:
    loc = finding["location"]
    path = loc["path"] or "(controller level)"
    if loc["kind"] == "PROCESS_GROUP":
        return path
    return f"{path} > {loc['name']}"


def print_report(report: dict[str, Any], console: Console, *, verbose: bool = False) -> None:
    summary = report["summary"]
    catalog = report["catalog"]
    console.print(
        f"[bold]flowport[/bold] {report['tool']['version']}  "
        f"NiFi {catalog['source_version']} -> {catalog['target_version']}  "
        f"input: {report['input']['name']} ({report['input']['kind']})"
    )
    console.print(
        f"{summary['process_groups']} process groups, {summary['components']} components, "
        f"[bold]{summary['total']} findings[/bold]"
    )

    for warning in report.get("warnings", []):
        console.print(f"[yellow]warning:[/yellow] {warning}")

    table = Table(title="By severity", show_header=True, header_style="bold")
    table.add_column("Severity")
    table.add_column("Count", justify="right")
    for severity in SEVERITY_ORDER:
        count = summary["by_severity"][severity.value]
        table.add_row(Text(severity.value, style=STYLES[severity.value]), str(count))
    console.print(table)

    if summary["by_category"]:
        table = Table(title="By category", show_header=True, header_style="bold")
        table.add_column("Category")
        table.add_column("Count", justify="right")
        for category, count in summary["by_category"].items():
            table.add_row(category, str(count))
        console.print(table)

    findings = report["findings"]
    for severity in SEVERITY_ORDER:
        group = [f for f in findings if f["severity"] == severity.value]
        if not group:
            continue
        console.rule(Text(f"{severity.value} ({len(group)})", style=STYLES[severity.value]))
        for finding in group:
            console.print(
                Text(finding["rule_id"], style=STYLES[severity.value]),
                Text(_location(finding), style="bold"),
            )
            lines = Text()
            if finding["location"]["type"]:
                lines.append(f"type: {finding['location']['type']}\n", style="dim")
            lines.append(f"id: {finding['location']['id']}\n", style="dim")
            lines.append(finding["message"])
            if finding["suggestion"]:
                lines.append("\n-> ", style="green")
                lines.append(finding["suggestion"])
            if verbose:
                for source in finding["sources"]:
                    lines.append(f"\n{source}", style="dim")
                if finding.get("reference"):
                    lines.append(f"\n{finding['reference']}", style="dim")
            console.print(Padding(lines, (0, 0, 0, 4)))
