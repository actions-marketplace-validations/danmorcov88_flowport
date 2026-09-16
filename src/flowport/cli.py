"""Command-line entry point."""

from __future__ import annotations

import typer

from flowport import __version__

app = typer.Typer(
    name="flowport",
    help="Analyze and migrate Apache NiFi 1.x flows for NiFi 2.x.",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"flowport {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show the version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Analyze and migrate Apache NiFi 1.x flows for NiFi 2.x."""
