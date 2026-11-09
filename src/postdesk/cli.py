"""The `postdesk` command.

Subcommands land with their milestones (docs/roadmap.md). Until then each
one says so rather than pretending.
"""

import typer

from postdesk import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)

NOT_YET = "not built yet: see docs/roadmap.md"


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command()
def doctor() -> None:
    """Check keys, database, renderer, Instagram token and public storage, in order."""
    typer.echo(f"doctor: {NOT_YET} (M0)")
    raise typer.Exit(1)


@app.command()
def serve() -> None:
    """Run the API, the web UI, the worker and the scheduler loop."""
    typer.echo(f"serve: {NOT_YET} (M0)")
    raise typer.Exit(1)


@app.command()
def cron() -> None:
    """One pass: publish what is due, then collect insights."""
    typer.echo(f"cron: {NOT_YET} (M0)")
    raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
