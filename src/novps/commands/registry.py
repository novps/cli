from __future__ import annotations

import typer

from novps.client import get_client
from novps.output import output

app = typer.Typer(no_args_is_help=True)

COLUMNS = [
    ("id", "ID"),
    ("name", "Name"),
    ("plan", "Plan"),
    ("created_at", "Created At"),
]


@app.command("list")
def list_registry(
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List registry namespaces."""
    client = get_client()
    resp = client.get("/public-api/registry")
    data = resp.get("data", [])
    output(data, COLUMNS, title="Registry Namespaces", as_json=json)
