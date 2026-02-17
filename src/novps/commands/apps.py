from __future__ import annotations

import typer

from novps.client import get_client
from novps.output import output

app = typer.Typer(no_args_is_help=True)

APP_COLUMNS = [
    ("id", "ID"),
    ("name", "Name"),
    ("namespace", "Namespace"),
    ("resources_count", "Resources"),
    ("created_at", "Created At"),
]

RESOURCE_COLUMNS = [
    ("id", "ID"),
    ("name", "Name"),
    ("type", "Type"),
    ("internal_domain", "Internal Domain"),
    ("replicas_type", "Replicas Type"),
    ("replicas_count", "Replicas"),
    ("image_name", "Image"),
    ("image_tag", "Tag"),
    ("http_port", "HTTP Port"),
    ("internal_ports", "Internal Ports"),
]


@app.command("list")
def list_apps(
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List applications."""
    client = get_client()
    resp = client.get("/apps")
    data = resp.get("data", [])
    output(data, APP_COLUMNS, title="Applications", as_json=json)


@app.command()
def resources(
    app_id: str = typer.Argument(help="Application ID."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List resources for an application."""
    client = get_client()
    resp = client.get(f"/apps/{app_id}/resources")
    data = resp.get("data", [])
    output(data, RESOURCE_COLUMNS, title="Resources", as_json=json)
