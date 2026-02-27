from __future__ import annotations

import typer
from rich.table import Table

from novps.client import get_client
from novps.output import console, output, print_json

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


@app.command("get")
def get_database(
    database_id: str = typer.Argument(help="Database ID."),
    show_password: bool = typer.Option(False, "--show-password", help="Include password in connection details."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Show detailed information for a database."""
    client = get_client(project)
    include_password = "true" if show_password else "false"
    resp = client.get(f"/databases/{database_id}?include_password={include_password}")
    data = resp.get("data", {})

    if json:
        print_json(data)
        return

    engine = data.get("engine", "")
    version = data.get("version")

    console.print("")
    # ── Main database table ──────────────────────────────────────────
    table = Table(title=data.get("name", "Database"))
    table.add_column("Field", style="bold")
    table.add_column("Value")

    table.add_row("Name", data.get("name", ""))

    engine_display = engine
    if version:
        engine_display = f"{engine} {version}"
    table.add_row("Engine", engine_display)

    table.add_row("Node Count", str(data.get("node_count", "")))

    node_config = data.get("node_config", {})
    if node_config:
        table.add_row("Node Config", node_config.get("human_readable", ""))

    console.print(table)

    # ── Connection details table ─────────────────────────────────────
    connection = data.get("connection", {})
    if connection:
        conn_table = Table(title="Connection Details")
        conn_table.add_column("Field", style="bold")
        conn_table.add_column("Value")

        if connection.get("username"):
            conn_table.add_row("Username", connection["username"])

        password = connection.get("password") if show_password else None
        conn_table.add_row("Password", password or "<hidden>")

        if connection.get("internal_host"):
            conn_table.add_row("Internal Host", connection["internal_host"])
        if connection.get("internal_port"):
            conn_table.add_row("Internal Port", str(connection["internal_port"]))

        console.print(conn_table)

    # ── Readonly replica (postgres only) ─────────────────────────────
    replica = data.get("readonly_replica")
    if replica:
        replica_table = Table(title="Read-only Replica")
        replica_table.add_column("Field", style="bold")
        replica_table.add_column("Value")

        replica_table.add_row("Name", replica.get("name", ""))

        replica_node_config = replica.get("node_config", {})
        if replica_node_config:
            replica_table.add_row("Node Config", replica_node_config.get("human_readable", ""))

        replica_connection = replica.get("connection", {})
        if replica_connection:
            if replica_connection.get("internal_host"):
                replica_table.add_row("Internal Host", replica_connection["internal_host"])
            if replica_connection.get("internal_port"):
                replica_table.add_row("Internal Port", str(replica_connection["internal_port"]))

        console.print(replica_table)
