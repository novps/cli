from __future__ import annotations

import typer

from novps.client import get_client
from novps.output import output

app = typer.Typer(no_args_is_help=True)

COLUMNS = [
    ("id", "ID"),
    ("name", "Name"),
    ("access_level", "Access Level"),
    ("created_at", "Created At"),
]


@app.command("list")
def list_storage(
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List S3 buckets."""
    client = get_client()
    resp = client.get("/storage")
    data = resp.get("data", [])
    output(data, COLUMNS, title="S3 Buckets", as_json=json)
