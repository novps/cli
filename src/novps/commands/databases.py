from __future__ import annotations

import typer

from novps.client import get_client
from novps.output import output

app = typer.Typer(no_args_is_help=True)

COLUMNS = [
    ("id", "ID"),
    ("name", "Name"),
    ("engine", "Engine"),
    ("status", "Status"),
    ("node_type", "Node Type"),
    ("node_count", "Nodes"),
]


@app.command("list")
def list_databases(
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """List databases."""
    client = get_client(project)
    resp = client.get("/databases")
    data = resp.get("data", [])
    output(data, COLUMNS, title="Databases", as_json=json)
