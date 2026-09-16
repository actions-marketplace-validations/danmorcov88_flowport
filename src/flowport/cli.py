"""Command-line entry point."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from flowport import __version__
from flowport.catalog import DEFAULT_SOURCE_VERSION, DEFAULT_TARGET_VERSION, CatalogError
from flowport.loaders import LoadError

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
