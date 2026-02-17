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
    with_values: bool = typer.Option(False, "--with-values", help="Include secret values in the output."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List secret keys for an application or resource."""
    client = get_client()
    include_values = "true" if with_values else "false"

    if resource_id:
        resp = client.get(f"/apps/{app_id}/resources/{resource_id}/secrets?include_values={include_values}")
        data = resp.get("data", [])
        columns = SECRET_COLUMNS + ([("value", "Value")] if with_values else [])
        output(data, columns, title=f"Secrets (resource: {resource_id})", as_json=json)
    else:
        resp = client.get(f"/apps/{app_id}/secrets?include_values={include_values}")
        data = resp.get("data", {})

        if json:
            print_json(data)
            return

        global_secrets = data.get("global", [])
        if global_secrets:
            table = Table(title="Global Secrets")
            table.add_column("Key")
            if with_values:
                table.add_column("Value")
            for s in global_secrets:
                row = [s.get("key", "")]
                if with_values:
                    row.append(s.get("value", ""))
                table.add_row(*row)
            console.print(table)

        resource_secrets = data.get("resources", {})
        for rid, secrets_list in resource_secrets.items():
            if not secrets_list:
                continue
            table = Table(title=f"Resource: {rid}")
            table.add_column("Key")
            if with_values:
                table.add_column("Value")
            for s in secrets_list:
                row = [s.get("key", "")]
                if with_values:
                    row.append(s.get("value", ""))
                table.add_row(*row)
            console.print(table)

        if not global_secrets and not any(resource_secrets.values()):
            typer.echo("No secrets found.")


@app.command("get")
def get_secret(
    app_id: str = typer.Argument(help="Application ID."),
    secret_key: str = typer.Argument(help="Secret key name (e.g. DATABASE_URL)."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Get a secret value by key name."""
    client = get_client()
    resp = client.get(f"/apps/{app_id}/secrets/{secret_key}")
    data = resp.get("data", {})

    if json:
        print_json(data)
        return

    table = Table(title="Secret")
    table.add_column("Key")
    table.add_column("Value")
    table.add_column("Resource ID")
    table.add_row(data.get("key", ""), data.get("value", ""), data.get("resource_id") or "global")
    console.print(table)
