"""Command-line interface for the battery health benchmark."""

import typer

app = typer.Typer(
    help="Reproducible battery state-of-health benchmarking tools.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Print the package version."""

    from battery_health import __version__

    typer.echo(__version__)
