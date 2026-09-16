"""Command-line entry point."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from flowport import __version__
from flowport.catalog import DEFAULT_SOURCE_VERSION, DEFAULT_TARGET_VERSION, CatalogError
from flowport.loaders import LoadError
from flowport.reports import SEVERITY_ORDER

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

app = typer.Typer(
    name="flowport",
    help="Analyze and migrate Apache NiFi 1.x flows for NiFi 2.x.",
    no_args_is_help=True,
    add_completion=False,
)
stderr = Console(stderr=True)
migrate_app = typer.Typer(
    help="Apply safe changes to a flow and write the result to a new file.",
    no_args_is_help=True,
)
app.add_typer(migrate_app, name="migrate")


class Format(StrEnum):
    terminal = "terminal"
    json = "json"
    markdown = "markdown"
    html = "html"


class FailOn(StrEnum):
    blocker = "blocker"
    manual = "manual"
    auto_fixable = "auto_fixable"
    info = "info"
    never = "never"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"flowport {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Show the version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Analyze and migrate Apache NiFi 1.x flows for NiFi 2.x."""


@app.command()
def analyze(
    flow_file: Annotated[
        Path, typer.Argument(help="flow.json.gz, flow.json or an exported flow definition JSON.")
    ],
    fmt: Annotated[
        Format, typer.Option("--format", "-f", help="Report format.", show_default=True)
    ] = Format.terminal,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write the report to this file instead of stdout."),
    ] = None,
    fail_on: Annotated[
        FailOn,
        typer.Option(
            "--fail-on",
            help="Exit with status 1 when a finding of this severity or higher exists.",
            show_default=True,
        ),
    ] = FailOn.blocker,
    source_version: Annotated[
        str, typer.Option("--source-version", help="NiFi release the flow comes from.")
    ] = DEFAULT_SOURCE_VERSION,
    target_version: Annotated[
        str, typer.Option("--target-version", help="NiFi release to migrate to.")
    ] = DEFAULT_TARGET_VERSION,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show sources in terminal output.")
    ] = False,
) -> None:
    """Report everything in a NiFi 1.x flow that will not work on NiFi 2.x."""
    from flowport.catalog import load_catalog
    from flowport.loaders import load
    from flowport.reports import build_report, render
    from flowport.rules import Severity
    from flowport.rules import analyze as run_rules

    try:
        catalog = load_catalog(source_version, target_version)
        flow = load(flow_file)
    except (LoadError, CatalogError) as exc:
        stderr.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(EXIT_ERROR) from None

    findings = run_rules(flow, catalog)
    report = build_report(flow, findings, catalog, flow_file)

    if fmt is Format.terminal:
        from flowport.reports.terminal import print_report

        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("w", encoding="utf-8") as fh:
                print_report(report, Console(file=fh, width=100), verbose=verbose)
        else:
            print_report(report, Console(), verbose=verbose)
    else:
        for warning in report["warnings"]:
            stderr.print(f"[yellow]warning:[/yellow] {warning}")
        text = render(report, fmt.value)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8", newline="\n")
        else:
            typer.echo(text, nl=False)

    if fail_on is not FailOn.never:
        threshold = Severity(fail_on.value.upper())
        if any(f.severity.rank >= threshold.rank for f in findings):
            raise typer.Exit(EXIT_FINDINGS)
    raise typer.Exit(EXIT_OK)


@migrate_app.command("variables")
def migrate_variables_command(
    flow_file: Annotated[
        Path, typer.Argument(help="flow.json.gz, flow.json or an exported flow definition JSON.")
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Where to write the migrated flow (gzip when the name ends in .gz). "
            "changes.json and report.md are written next to it.",
        ),
    ],
    keep_unused: Annotated[
        bool,
        typer.Option("--keep-unused", help="Also convert variables that nothing references."),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the changes without writing any file.")
    ] = False,
    source_version: Annotated[
        str, typer.Option("--source-version", help="NiFi release the flow comes from.")
    ] = DEFAULT_SOURCE_VERSION,
    target_version: Annotated[
        str, typer.Option("--target-version", help="NiFi release to migrate to.")
    ] = DEFAULT_TARGET_VERSION,
) -> None:
    """Convert process group variables to parameter contexts.

    Creates one parameter context per process group that defines variables
    (inheriting from the nearest ancestor context), rewrites ${name} to #{name}
    where that is safe, and removes each variable once all its references are
    rewritten. Everything that needs a decision stays in the report. The output
    is still a NiFi 1.x flow, so it can be checked on 1.x before upgrading.
    """
    from flowport.catalog import load_catalog
    from flowport.loaders import load
    from flowport.reports import build_report, render
    from flowport.rules import Finding
    from flowport.rules import analyze as run_rules
    from flowport.transforms import CHANGE_KINDS, render_changes
    from flowport.transforms.variables import migrate_variables
    from flowport.writers import write

    try:
        catalog = load_catalog(source_version, target_version)
        flow = load(flow_file)
    except (LoadError, CatalogError) as exc:
        stderr.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(EXIT_ERROR) from None
    if output.resolve() == flow_file.resolve():
        stderr.print("[red]error:[/red] the output must be a different file than the input")
        raise typer.Exit(EXIT_ERROR)

    result = migrate_variables(flow, catalog, keep_unused=keep_unused)
    result.flow.source_name = output.name
    findings = sorted([*run_rules(result.flow, catalog), *result.findings], key=Finding.sort_key)
    counts = result.counts()

    console = Console()
    console.print(
        f"[bold]flowport[/bold] {__version__}  migrate variables  "
        f"input: {flow_file.name} ({flow.kind.value})"
    )
    table = Table(title="Changes", show_header=True, header_style="bold")
    table.add_column("Kind")
    table.add_column("Count", justify="right")
    for kind in CHANGE_KINDS:
        table.add_row(kind, str(counts[kind]))
    console.print(table)
    if dry_run:
        details = Table(show_header=True, header_style="bold")
        for column in ("Kind", "Where", "Name", "Old", "New", "Context"):
            details.add_column(column, overflow="fold")
        for change in result.changes:
            where = change.path
            if change.kind == "rewrite-property":
                where = f"{change.path} > {change.component_name}"
            details.add_row(
                change.kind,
                where,
                change.property or "",
                change.old or "",
                change.new or "",
                change.context or "",
            )
        console.print(details)
    by_severity = {s.value: sum(1 for f in findings if f.severity is s) for s in SEVERITY_ORDER}
    remaining = ", ".join(f"{k} {v}" for k, v in by_severity.items() if v)
    console.print(
        f"Findings after migration: {len(findings)}" + (f" ({remaining})" if remaining else "")
    )
    if dry_run:
        console.print("[yellow]dry run:[/yellow] nothing written")
        raise typer.Exit(EXIT_OK)

    write(result.flow, output)
    report = build_report(result.flow, findings, catalog, output)
    report["migration"] = {
        "command": "migrate variables",
        "input": flow_file.name,
        "keep_unused": keep_unused,
        "changes": counts,
    }
    changes_path = output.parent / "changes.json"
    report_path = output.parent / "report.md"
    changes_path.write_text(render_changes(result.changes), encoding="utf-8", newline="\n")
    report_path.write_text(render(report, "markdown"), encoding="utf-8", newline="\n")
    console.print(f"Wrote {output}, {changes_path} and {report_path}")
    raise typer.Exit(EXIT_OK)
