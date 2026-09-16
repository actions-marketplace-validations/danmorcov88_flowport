"""Command-line entry point."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from flowport import __version__
from flowport.catalog import DEFAULT_SOURCE_VERSION, DEFAULT_TARGET_VERSION, Catalog, CatalogError
from flowport.loaders import LoadError
from flowport.model import Flow
from flowport.reports import SEVERITY_ORDER
from flowport.transforms import MigrationResult

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


_FLOW_FILE_HELP = "flow.json.gz, flow.json or an exported flow definition JSON."
_OUTPUT_HELP = (
    "Where to write the migrated flow (gzip when the name ends in .gz). "
    "changes.json and report.md are written next to it."
)
_DRY_RUN_HELP = "Show the changes without writing any file."
_SOURCE_HELP = "NiFi release the flow comes from."
_TARGET_HELP = "NiFi release to migrate to."


def _load_for_migration(
    flow_file: Path, output: Path, source_version: str, target_version: str
) -> tuple[Flow, Catalog]:
    from flowport.catalog import load_catalog
    from flowport.loaders import load

    try:
        catalog = load_catalog(source_version, target_version)
        flow = load(flow_file)
    except (LoadError, CatalogError) as exc:
        stderr.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(EXIT_ERROR) from None
    if output.resolve() == flow_file.resolve():
        stderr.print("[red]error:[/red] the output must be a different file than the input")
        raise typer.Exit(EXIT_ERROR)
    return flow, catalog


def _run_flow_migration(
    command: str,
    flow_file: Path,
    output: Path,
    *,
    dry_run: bool,
    source_version: str,
    target_version: str,
    migrate: Callable[[Flow, Catalog], MigrationResult],
    report_extra: dict[str, Any] | None = None,
    console: Console | None = None,
) -> tuple[MigrationResult, Catalog]:
    """Load, migrate, print a summary and write flow + changes.json + report.md.

    Returns the result so that a caller can go on (``migrate all``); on a dry
    run nothing is written.
    """
    from flowport.reports import build_report, render
    from flowport.rules import Finding
    from flowport.rules import analyze as run_rules
    from flowport.transforms import CHANGE_KINDS, render_changes
    from flowport.writers import write

    flow, catalog = _load_for_migration(flow_file, output, source_version, target_version)
    result = migrate(flow, catalog)
    result.flow.source_name = output.name
    findings = sorted([*run_rules(result.flow, catalog), *result.findings], key=Finding.sort_key)
    counts = result.counts()

    console = console or Console()
    console.print(
        f"[bold]flowport[/bold] {__version__}  {command}  "
        f"input: {flow_file.name} ({flow.kind.value})"
    )
    table = Table(title="Changes", show_header=True, header_style="bold")
    table.add_column("Kind")
    table.add_column("Count", justify="right")
    for kind in CHANGE_KINDS:
        if counts[kind]:
            table.add_row(kind, str(counts[kind]))
    if not result.changes:
        table.add_row("(none)", "0")
    console.print(table)
    if dry_run:
        details = Table(show_header=True, header_style="bold")
        for column in ("Kind", "Where", "Name", "Old", "New", "Context"):
            details.add_column(column, overflow="fold")
        for change in result.changes:
            details.add_row(
                change.kind,
                f"{change.path} > {change.component_name}",
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
        return result, catalog

    write(result.flow, output)
    report = build_report(result.flow, findings, catalog, output)
    report["migration"] = {
        "command": command,
        "input": flow_file.name,
        "keep_unused": False,
        "note": "The findings below describe the migrated flow; see `changes.json` for every edit.",
        "changes": {k: v for k, v in counts.items() if v},
        **(report_extra or {}),
    }
    changes_path = output.parent / "changes.json"
    report_path = output.parent / "report.md"
    changes_path.write_text(render_changes(result.changes), encoding="utf-8", newline="\n")
    report_path.write_text(render(report, "markdown"), encoding="utf-8", newline="\n")
    console.print(f"Wrote {output}, {changes_path} and {report_path}")
    return result, catalog


@migrate_app.command("variables")
def migrate_variables_command(
    flow_file: Annotated[Path, typer.Argument(help=_FLOW_FILE_HELP)],
    output: Annotated[Path, typer.Option("--output", "-o", help=_OUTPUT_HELP)],
    keep_unused: Annotated[
        bool,
        typer.Option("--keep-unused", help="Also convert variables that nothing references."),
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help=_DRY_RUN_HELP)] = False,
    source_version: Annotated[
        str, typer.Option("--source-version", help=_SOURCE_HELP)
    ] = DEFAULT_SOURCE_VERSION,
    target_version: Annotated[
        str, typer.Option("--target-version", help=_TARGET_HELP)
    ] = DEFAULT_TARGET_VERSION,
) -> None:
    """Convert process group variables to parameter contexts.

    Creates one parameter context per process group that defines variables
    (inheriting from the nearest ancestor context), rewrites ${name} to #{name}
    where that is safe, and removes each variable once all its references are
    rewritten. Everything that needs a decision stays in the report. The output
    is still a NiFi 1.x flow, so it can be checked on 1.x before upgrading.
    """
    from flowport.transforms.variables import migrate_variables

    _run_flow_migration(
        "migrate variables",
        flow_file,
        output,
        dry_run=dry_run,
        source_version=source_version,
        target_version=target_version,
        migrate=lambda flow, catalog: migrate_variables(flow, catalog, keep_unused=keep_unused),
        report_extra={"keep_unused": keep_unused},
    )
    raise typer.Exit(EXIT_OK)


@migrate_app.command("components")
def migrate_components_command(
    flow_file: Annotated[Path, typer.Argument(help=_FLOW_FILE_HELP)],
    output: Annotated[Path, typer.Option("--output", "-o", help=_OUTPUT_HELP)],
    dry_run: Annotated[bool, typer.Option("--dry-run", help=_DRY_RUN_HELP)] = False,
    source_version: Annotated[
        str, typer.Option("--source-version", help=_SOURCE_HELP)
    ] = DEFAULT_SOURCE_VERSION,
    target_version: Annotated[
        str, typer.Option("--target-version", help=_TARGET_HELP)
    ] = DEFAULT_TARGET_VERSION,
) -> None:
    """Replace components with their documented successors.

    Applies only the 1:1 replacements listed in the catalog (replacements.yaml):
    the new type and bundle, property names, values and relationships, exactly
    as the Apache migration guide documents them. Components keep their id,
    name, position and connections. Processors scheduled EVENT_DRIVEN, which
    stops NiFi 2.x from starting, are switched to TIMER_DRIVEN. Everything
    without a documented replacement stays a finding.
    """
    from flowport.transforms.components import migrate_components

    _run_flow_migration(
        "migrate components",
        flow_file,
        output,
        dry_run=dry_run,
        source_version=source_version,
        target_version=target_version,
        migrate=migrate_components,
    )
    raise typer.Exit(EXIT_OK)


@migrate_app.command("all")
def migrate_all_command(
    flow_file: Annotated[Path, typer.Argument(help=_FLOW_FILE_HELP)],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Directory for the migrated flow (same file name as the input), changes.json, "
            "report.md and templates/.",
        ),
    ],
    keep_unused: Annotated[
        bool,
        typer.Option("--keep-unused", help="Also convert variables that nothing references."),
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help=_DRY_RUN_HELP)] = False,
    source_version: Annotated[
        str, typer.Option("--source-version", help=_SOURCE_HELP)
    ] = DEFAULT_SOURCE_VERSION,
    target_version: Annotated[
        str, typer.Option("--target-version", help=_TARGET_HELP)
    ] = DEFAULT_TARGET_VERSION,
) -> None:
    """Run every migration: variables, then components, then templates.

    Writes the migrated flow, one changes.json and one report.md for the flow,
    and a flow definition plus report for every template under templates/.
    """
    from flowport.transforms.pipeline import migrate_all

    console = Console()
    flow_output = output / flow_file.name
    holder: dict[str, Any] = {}

    def run(flow: Flow, catalog: Catalog) -> MigrationResult:
        pipeline = migrate_all(flow, catalog, keep_unused=keep_unused)
        holder["templates"] = pipeline.templates
        return pipeline.flow_result

    _, catalog = _run_flow_migration(
        "migrate all",
        flow_file,
        flow_output,
        dry_run=dry_run,
        source_version=source_version,
        target_version=target_version,
        migrate=run,
        report_extra={
            "keep_unused": keep_unused,
            "note": "Variables, components and templates were migrated. The findings below "
            "describe the migrated flow; see `changes.json` for every edit and templates/ "
            "for the converted templates.",
        },
        console=console,
    )
    templates = holder.get("templates") or []
    console.print(f"Templates converted: {len(templates)}")
    if dry_run:
        raise typer.Exit(EXIT_OK)
    for name, item in zip(
        _unique_slugs([c.source.name for c in templates]), templates, strict=True
    ):
        _write_converted_template(
            item, output / "templates" / f"{name}.json", flow_file.name, catalog, console
        )
    raise typer.Exit(EXIT_OK)


def _unique_slugs(names: list[str]) -> list[str]:
    """File-name slugs for template names, numbered when two templates collide."""
    from flowport.transforms.templates import slugify

    used: set[str] = set()
    result: list[str] = []
    for name in names:
        slug = slugify(name)
        candidate, counter = slug, 1
        while candidate in used:
            counter += 1
            candidate = f"{slug}-{counter}"
        used.add(candidate)
        result.append(candidate)
    return result


def _write_converted_template(
    converted: Any, output: Path, source_name: str, catalog: Any, console: Console
) -> None:
    """Write the definition and its report next to it (``<stem>.report.md``)."""
    from flowport.reports import build_report, render
    from flowport.writers import write_document

    write_document(converted.document, output)
    converted.flow.source_name = output.name
    report = build_report(converted.flow, converted.findings, catalog, output)
    report["migration"] = {
        "command": "migrate templates",
        "input": f"{source_name}: template '{converted.source.name}'",
        "keep_unused": False,
        "note": "The findings below describe the converted flow definition, which "
        "'Upload flow definition' imports on NiFi 1.x and 2.x.",
        "changes": {f"{k} converted": v for k, v in converted.counts().items() if v},
    }
    report_path = output.with_name(f"{output.stem}.report.md")
    report_path.write_text(render(report, "markdown"), encoding="utf-8", newline="\n")
    by_severity = {
        s.value: sum(1 for f in converted.findings if f.severity is s) for s in SEVERITY_ORDER
    }
    remaining = ", ".join(f"{k} {v}" for k, v in by_severity.items() if v)
    console.print(
        f"  {converted.source.name!r} -> {output} ({len(converted.findings)} findings"
        + (f": {remaining})" if remaining else ")")
    )


@migrate_app.command("templates")
def migrate_templates_command(
    flow_file: Annotated[Path, typer.Argument(help="flow.json.gz or flow.json holding templates.")],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Directory for the flow definitions: one <template>.json and "
            "<template>.report.md per template.",
        ),
    ],
    source_version: Annotated[
        str, typer.Option("--source-version", help="NiFi release the flow comes from.")
    ] = DEFAULT_SOURCE_VERSION,
    target_version: Annotated[
        str, typer.Option("--target-version", help="NiFi release to migrate to.")
    ] = DEFAULT_TARGET_VERSION,
) -> None:
    """Convert every template stored in a flow to a flow definition.

    NiFi 2.x removed templates and drops them when the flow loads. Each
    template becomes a flow definition JSON that "Upload flow definition"
    accepts on 1.x and 2.x. The analyzer runs on every result; its findings go
    to the report next to the definition.
    """
    from flowport.catalog import load_catalog
    from flowport.loaders import load
    from flowport.loaders.templates import templates_in_flow
    from flowport.model import InputKind
    from flowport.transforms.templates import convert_and_analyze

    try:
        catalog = load_catalog(source_version, target_version)
        flow = load(flow_file)
    except (LoadError, CatalogError) as exc:
        stderr.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(EXIT_ERROR) from None
    if flow.kind is not InputKind.FLOW:
        stderr.print(
            "[red]error:[/red] templates are stored in flow.json; this is a flow definition"
        )
        raise typer.Exit(EXIT_ERROR)

    console = Console()
    sources = templates_in_flow(flow.raw)
    console.print(
        f"[bold]flowport[/bold] {__version__}  migrate templates  "
        f"input: {flow_file.name} ({len(sources)} templates)"
    )
    if not sources:
        console.print("No templates in this flow; nothing written.")
        raise typer.Exit(EXIT_OK)
    converted = [convert_and_analyze(source, catalog) for source in sources]
    for name, item in zip(
        _unique_slugs([c.source.name for c in converted]), converted, strict=True
    ):
        _write_converted_template(item, output / f"{name}.json", flow_file.name, catalog, console)
    raise typer.Exit(EXIT_OK)


@migrate_app.command("template")
def migrate_template_command(
    template_file: Annotated[Path, typer.Argument(help="An XML template exported from NiFi 1.x.")],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="The flow definition to write; the report goes to <name>.report.md next to it.",
        ),
    ],
    source_version: Annotated[
        str, typer.Option("--source-version", help="NiFi release the template comes from.")
    ] = DEFAULT_SOURCE_VERSION,
    target_version: Annotated[
        str, typer.Option("--target-version", help="NiFi release to migrate to.")
    ] = DEFAULT_TARGET_VERSION,
) -> None:
    """Convert one exported XML template to a flow definition."""
    from flowport.catalog import load_catalog
    from flowport.loaders.templates import parse_template_xml
    from flowport.transforms.templates import convert_and_analyze

    try:
        catalog = load_catalog(source_version, target_version)
        if not template_file.is_file():
            raise LoadError(f"{template_file}: no such file")
        source = parse_template_xml(template_file.read_bytes())
    except (LoadError, CatalogError) as exc:
        stderr.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(EXIT_ERROR) from None
    if output.resolve() == template_file.resolve():
        stderr.print("[red]error:[/red] the output must be a different file than the input")
        raise typer.Exit(EXIT_ERROR)

    console = Console()
    console.print(
        f"[bold]flowport[/bold] {__version__}  migrate template  input: {template_file.name}"
    )
    converted = convert_and_analyze(source, catalog)
    _write_converted_template(converted, output, template_file.name, catalog, console)
    raise typer.Exit(EXIT_OK)


class ValidateFormat(StrEnum):
    terminal = "terminal"
    json = "json"


@app.command()
def validate(
    flow_file: Annotated[Path, typer.Argument(help=_FLOW_FILE_HELP)],
    nifi_url: Annotated[
        str | None,
        typer.Option(
            "--nifi-url",
            help="Base URL of a running NiFi, for example https://localhost:8443/nifi-api.",
        ),
    ] = None,
    username: Annotated[
        str | None,
        typer.Option("--username", envvar="FLOWPORT_NIFI_USERNAME", help="Single-user login."),
    ] = None,
    password: Annotated[
        str | None,
        typer.Option("--password", envvar="FLOWPORT_NIFI_PASSWORD", help="Single-user password."),
    ] = None,
    insecure: Annotated[
        bool, typer.Option("--insecure", help="Do not verify the TLS certificate.")
    ] = False,
    use_docker: Annotated[
        bool,
        typer.Option("--docker", help="Start a throwaway NiFi in Docker instead of --nifi-url."),
    ] = False,
    image: Annotated[
        str, typer.Option("--image", help="Docker image for --docker.")
    ] = "apache/nifi:2.12.0",
    port: Annotated[
        int, typer.Option("--port", help="Host and container port for --docker.")
    ] = 18443,
    keep: Annotated[
        bool,
        typer.Option(
            "--keep",
            help="Leave the imported process group (and the Docker container) in place.",
        ),
    ] = False,
    fmt: Annotated[
        ValidateFormat, typer.Option("--format", "-f", help="Output format.", show_default=True)
    ] = ValidateFormat.terminal,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write the result to this file instead of stdout."),
    ] = None,
) -> None:
    """Import a flow into a running NiFi and report what NiFi says about it.

    The flow is uploaded as a new process group (the same path as "Upload flow
    definition"), its components are validated by NiFi itself, and the group
    is removed afterwards unless --keep is given. Exit code 1 when a component
    is invalid or its type is missing on the target; 2 on errors.
    """
    import json

    from flowport.loaders import load
    from flowport.nifi import NiFiClient, NiFiError
    from flowport.validation import validate_flow

    if bool(nifi_url) == use_docker:
        stderr.print("[red]error:[/red] give either --nifi-url or --docker")
        raise typer.Exit(EXIT_ERROR)
    try:
        flow = load(flow_file)
    except LoadError as exc:
        stderr.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(EXIT_ERROR) from None

    container = None
    try:
        if use_docker:
            from flowport.nifi.docker import start_container

            stderr.print(f"starting {image} on port {port} (this takes a minute or two)")
            container = start_container("flowport-validate", image, port, https=True)
            client = container.wait_ready()
        else:
            assert nifi_url is not None
            client = NiFiClient(
                nifi_url, username=username, password=password, verify_tls=not insecure
            )
        result = validate_flow(flow, client, keep=keep)
    except NiFiError as exc:
        stderr.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(EXIT_ERROR) from None
    finally:
        if container is not None and not keep:
            container.remove()
    if container is not None and keep:
        stderr.print(f"container {container.name} left running at {container.url}")

    if fmt is ValidateFormat.json:
        text = json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n"
    else:
        from io import StringIO

        buffer = StringIO()
        console = Console(file=buffer, width=100) if output else Console()
        summary = result.summary()
        console.print(
            f"[bold]flowport[/bold] {__version__}  validate  input: {flow_file.name}  "
            f"NiFi {result.nifi_version} at {result.nifi_url}"
        )
        for warning in result.warnings:
            console.print(f"[yellow]warning:[/yellow] {warning}")
        console.print(
            f"{summary['components']} components: [green]{summary['valid']} valid[/green], "
            f"[red]{summary['invalid']} invalid[/red], [red]{summary['ghosts']} missing types[/red]"
        )
        for component in result.invalid:
            label = "missing type" if component.ghost else component.status
            console.print(f"[bold]{component.path} > {component.name}[/bold] ({label})")
            console.print(f"  {component.type}")
            for error in component.errors:
                console.print(f"  - {error}")
        if result.kept:
            console.print(f"process group {result.group_name!r} ({result.group_id}) kept")
        text = buffer.getvalue()
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8", newline="\n")
    elif text:
        typer.echo(text, nl=False)
    raise typer.Exit(EXIT_FINDINGS if result.invalid else EXIT_OK)
