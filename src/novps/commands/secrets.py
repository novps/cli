from __future__ import annotations

import typer

from novps.client import get_client
from novps.output import console, output, print_json

from rich.table import Table

app = typer.Typer(no_args_is_help=True)

SECRET_COLUMNS = [
    ("id", "ID"),
    ("key", "Key"),
]


@app.command("list")
def list_secrets(
    app_id: str = typer.Argument(help="Application ID."),
    resource_id: str | None = typer.Option(None, "--resource", "-r", help="Resource ID (for resource-level secrets)."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List secret keys for an application or resource."""
    client = get_client()

    if resource_id:
        resp = client.get(f"/public-api/apps/{app_id}/resources/{resource_id}/secrets?include_values=false")
        data = resp.get("data", [])
        output(data, SECRET_COLUMNS, title=f"Secrets (resource: {resource_id})", as_json=json)
    else:
        resp = client.get(f"/public-api/apps/{app_id}/secrets?include_values=false")
        data = resp.get("data", {})

        if json:
            print_json(data)
            return

        global_secrets = data.get("global", [])
        if global_secrets:
            table = Table(title="Global Secrets")
            table.add_column("ID")
            table.add_column("Key")
            for s in global_secrets:
                table.add_row(str(s.get("id", "")), s.get("key", ""))
            console.print(table)

        resource_secrets = data.get("resources", {})
        for rid, secrets_list in resource_secrets.items():
            if not secrets_list:
                continue
            table = Table(title=f"Resource: {rid}")
            table.add_column("ID")
            table.add_column("Key")
            for s in secrets_list:
                table.add_row(str(s.get("id", "")), s.get("key", ""))
            console.print(table)

        if not global_secrets and not any(resource_secrets.values()):
            typer.echo("No secrets found.")


@app.command("get")
def get_secret(
    app_id: str = typer.Argument(help="Application ID."),
    secret_id: str = typer.Argument(help="Secret ID."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Get a secret value."""
    client = get_client()
    resp = client.get(f"/public-api/apps/{app_id}/secrets/{secret_id}")
    data = resp.get("data", {})

    if json:
        print_json(data)
        return

    table = Table(title="Secret")
    table.add_column("Key")
    table.add_column("Value")
    table.add_row(data.get("key", ""), data.get("value", ""))
    console.print(table)
