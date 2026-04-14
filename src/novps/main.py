from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _pkg_version

import typer

from novps.commands import apps, auth, databases, port_forward, registry, resources, secrets, storage

app = typer.Typer(name="novps", help="CLI tool for novps.io infrastructure management.", no_args_is_help=True)

app.add_typer(auth.app, name="auth", help="Authentication commands.")
app.add_typer(apps.app, name="apps", help="Application management.")
app.add_typer(secrets.app, name="secrets", help="Secrets management.")
app.add_typer(databases.app, name="databases", help="Database management.")
app.add_typer(registry.app, name="registry", help="Registry management.")
app.add_typer(resources.app, name="resources", help="Resource management.")
app.add_typer(storage.app, name="storage", help="Storage management.")
app.add_typer(port_forward.app, name="port-forward", help="Port forwarding to resources and databases.")


def _get_version() -> str:
    try:
        from novps._version import __version__
        return __version__
    except ImportError:
        pass
    try:
        return _pkg_version("novps")
    except PackageNotFoundError:
        return "unknown"


@app.command("version")
def version_command() -> None:
    """Show the CLI version."""
    typer.echo(_get_version())


if __name__ == "__main__":
    app()
