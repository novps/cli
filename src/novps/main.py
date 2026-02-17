from __future__ import annotations

import typer

from novps.commands import apps, auth, databases, port_forward, registry, secrets, storage

app = typer.Typer(name="novps", help="CLI tool for novps.io infrastructure management.", no_args_is_help=True)

app.add_typer(auth.app, name="auth", help="Authentication commands.")
app.add_typer(apps.app, name="apps", help="Application management.")
app.add_typer(secrets.app, name="secrets", help="Secrets management.")
app.add_typer(databases.app, name="databases", help="Database management.")
app.add_typer(registry.app, name="registry", help="Registry management.")
app.add_typer(storage.app, name="storage", help="Storage management.")
app.add_typer(port_forward.app, name="port-forward", help="Port forwarding to resources and databases.")

if __name__ == "__main__":
    app()
