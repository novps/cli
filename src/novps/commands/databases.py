from __future__ import annotations

import time
from typing import Any

import typer
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from novps.client import get_client
from novps.output import console, output, print_json

WAIT_POLL_INTERVAL = 3
WAIT_TIMEOUT_SECONDS = 15 * 60
TERMINAL_STATUSES = {"created", "error"}
BACKUP_TERMINAL_STATUSES = {"completed", "error"}
REPLICA_TERMINAL_STATUSES = {"available"}

app = typer.Typer(no_args_is_help=True)
replica_app = typer.Typer(no_args_is_help=True, help="Manage read-only replicas (postgres only).")
backups_app = typer.Typer(no_args_is_help=True, help="Manage postgres/mysql backups.")
pool_app = typer.Typer(no_args_is_help=True, help="Manage postgres connection pools.")
db_app = typer.Typer(no_args_is_help=True, help="Manage logical databases inside an instance (postgres/mysql).")
user_app = typer.Typer(no_args_is_help=True, help="Manage users inside an instance (postgres/mysql).")

app.add_typer(replica_app, name="replica")
app.add_typer(backups_app, name="backups")
app.add_typer(pool_app, name="pool")
app.add_typer(db_app, name="db")
app.add_typer(user_app, name="user")
# Backwards-compat aliases for the previous postgres-only naming.
app.add_typer(db_app, name="pg-db", hidden=True)
app.add_typer(user_app, name="pg-user", hidden=True)

DATABASE_COLUMNS = [
    ("id", "ID"),
    ("name", "Name"),
    ("engine", "Engine"),
    ("status", "Status"),
    ("node_type", "Node Type"),
    ("node_count", "Nodes"),
]

REPLICA_COLUMNS = [
    ("id", "ID"),
    ("size", "Size"),
    ("status", "Status"),
    ("created_at", "Created At"),
]

BACKUP_COLUMNS = [
    ("id", "ID"),
    ("name", "Name"),
    ("status", "Status"),
    ("size", "Size"),
    ("created_at", "Created At"),
]

POOL_COLUMNS = [
    ("id", "ID"),
    ("name", "Name"),
    ("target", "Target"),
    ("mode", "Mode"),
    ("size", "Size"),
    ("status", "Status"),
    ("created_at", "Created At"),
]

PGDB_COLUMNS = [
    ("id", "ID"),
    ("name", "Name"),
    ("created_at", "Created At"),
]


def _format_size(size: Any) -> str:
    if size is None or size == "":
        return "-"
    try:
        n = float(size)
    except (TypeError, ValueError):
        return str(size)
    if n <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.1f} {units[i]}" if i > 0 else f"{int(n)} {units[i]}"


def _wait_for_replica(client, database_id: str) -> dict[str, Any]:
    """Poll database until the replica reaches a terminal status. Returns replica dict."""
    deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}[/bold]"),
        TextColumn("status: [cyan]{task.fields[status]}[/cyan]"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    with progress:
        task_id = progress.add_task("Provisioning replica", status="scheduled")
        while True:
            try:
                resp = client.get(f"/databases/{database_id}")
            except typer.Exit:
                progress.stop()
                raise
            replica = (resp.get("data") or {}).get("readonly_replica") or {}
            status = replica.get("status") or "unknown"
            progress.update(task_id, status=status)

            if status in REPLICA_TERMINAL_STATUSES:
                return replica
            if time.monotonic() >= deadline:
                progress.stop()
                typer.echo(
                    f"Timed out after {WAIT_TIMEOUT_SECONDS}s waiting for replica. "
                    f"Last status: {status}.",
                    err=True,
                )
                raise typer.Exit(code=1)
            time.sleep(WAIT_POLL_INTERVAL)


def _wait_for_backup(client, database_id: str, backup_id: str) -> dict[str, Any]:
    """Poll until the backup reaches a terminal status. Returns the backup dict."""
    deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}[/bold]"),
        TextColumn("status: [cyan]{task.fields[status]}[/cyan]"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    with progress:
        task_id = progress.add_task("Creating backup", status="scheduled")
        while True:
            try:
                resp = client.get(f"/databases/{database_id}/backups")
            except typer.Exit:
                progress.stop()
                raise
            backups = resp.get("data") or []
            entry = next((b for b in backups if str(b.get("id")) == str(backup_id)), None)
            status = (entry or {}).get("status") or "unknown"
            progress.update(task_id, status=status)

            if status in BACKUP_TERMINAL_STATUSES:
                return entry or {}
            if time.monotonic() >= deadline:
                progress.stop()
                typer.echo(
                    f"Timed out after {WAIT_TIMEOUT_SECONDS}s waiting for backup. "
                    f"Last status: {status}.",
                    err=True,
                )
                raise typer.Exit(code=1)
            time.sleep(WAIT_POLL_INTERVAL)


def _print_connection_table(connection: dict[str, Any], *, show_password: bool) -> None:
    if not connection:
        return
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
    if connection.get("database"):
        conn_table.add_row("Database", connection["database"])
    if connection.get("database_url") and show_password:
        conn_table.add_row("Database URL", connection["database_url"])
    console.print(conn_table)


def _wait_for_database(client, database_id: str) -> str:
    """Poll the database until status is terminal. Returns final status."""
    deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}[/bold]"),
        TextColumn("status: [cyan]{task.fields[status]}[/cyan]"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    with progress:
        task_id = progress.add_task("Provisioning database", status="pending")
        while True:
            try:
                resp = client.get(f"/databases/{database_id}")
            except typer.Exit:
                progress.stop()
                raise
            status = (resp.get("data") or {}).get("status") or "unknown"
            progress.update(task_id, status=status)

            if status in TERMINAL_STATUSES:
                return status
            if time.monotonic() >= deadline:
                progress.stop()
                typer.echo(
                    f"Timed out after {WAIT_TIMEOUT_SECONDS}s waiting for database to become ready. "
                    f"Last status: {status}.",
                    err=True,
                )
                raise typer.Exit(code=1)
            time.sleep(WAIT_POLL_INTERVAL)


def _confirm_delete(message: str, *, force: bool) -> None:
    if force:
        return
    typer.echo(message)
    typed = typer.prompt("Type DELETE to confirm", default="", show_default=False)
    if typed != "DELETE":
        typer.echo("Aborted.", err=True)
        raise typer.Exit(code=1)


# ── databases: list / get / create / delete / resize / allow-apps ────────


@app.command("list")
def list_databases(
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """List databases."""
    client = get_client(project)
    resp = client.get("/databases")
    data = resp.get("data", [])
    output(data, DATABASE_COLUMNS, title="Databases", as_json=json)


def _print_get_table(data: dict[str, Any], show_password: bool) -> None:
    engine = data.get("engine", "")
    version = data.get("version")

    console.print("")
    table = Table(title=data.get("name", "Database"))
    table.add_column("Field", style="bold")
    table.add_column("Value")

    table.add_row("ID", str(data.get("id", "")))
    table.add_row("Name", data.get("name", ""))
    table.add_row("Status", data.get("status", ""))

    engine_display = f"{engine} {version}" if version else engine
    table.add_row("Engine", engine_display)
    table.add_row("Node Count", str(data.get("node_count", "")))

    node_config = data.get("node_config") or {}
    if node_config:
        table.add_row("Node Config", node_config.get("human_readable", ""))

    console.print(table)

    _print_connection_table(data.get("connection") or {}, show_password=show_password)

    allowed_apps = data.get("allowed_apps") or []
    if allowed_apps:
        apps_table = Table(title="Allowed Apps")
        apps_table.add_column("ID")
        apps_table.add_column("Name")
        for entry in allowed_apps:
            apps_table.add_row(str(entry.get("id", "")), entry.get("name", ""))
        console.print(apps_table)

    replica = data.get("readonly_replica")
    if replica:
        replica_table = Table(title="Read-only Replica")
        replica_table.add_column("Field", style="bold")
        replica_table.add_column("Value")
        replica_table.add_row("ID", str(replica.get("id", "")))
        replica_table.add_row("Name", replica.get("name", ""))
        replica_table.add_row("Size", replica.get("size", ""))
        replica_table.add_row("Status", replica.get("status", ""))

        replica_node_config = replica.get("node_config") or {}
        if replica_node_config:
            replica_table.add_row("Node Config", replica_node_config.get("human_readable", ""))

        replica_connection = replica.get("connection") or {}
        if replica_connection.get("internal_host"):
            replica_table.add_row("Internal Host", replica_connection["internal_host"])
        if replica_connection.get("internal_port"):
            replica_table.add_row("Internal Port", str(replica_connection["internal_port"]))
        if replica_connection.get("database_url") and show_password:
            replica_table.add_row("Database URL", replica_connection["database_url"])
        console.print(replica_table)


def _print_get_env(data: dict[str, Any], show_password: bool) -> None:
    connection = data.get("connection") or {}
    lines = [
        f"DB_ENGINE={data.get('engine', '')}",
        f"DB_HOST={connection.get('internal_host', '')}",
        f"DB_PORT={connection.get('internal_port', '')}",
        f"DB_USER={connection.get('username', '')}",
    ]
    if connection.get("database"):
        lines.append(f"DB_NAME={connection['database']}")
    if show_password:
        password = connection.get("password", "")
        lines.append(f"DB_PASSWORD={password}")
        if connection.get("database_url"):
            lines.append(f"DATABASE_URL={connection['database_url']}")
    else:
        lines.append("DB_PASSWORD=")
    typer.echo("\n".join(lines))


@app.command("get")
def get_database(
    database_id: str = typer.Argument(help="Database ID."),
    show_password: bool = typer.Option(False, "--show-password", help="Include password and DATABASE_URL."),
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table, json, env."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Show detailed information for a database."""
    if fmt not in ("table", "json", "env"):
        typer.echo("Error: --format must be one of: table, json, env", err=True)
        raise typer.Exit(code=1)

    client = get_client(project)
    include_password = "true" if show_password else "false"
    resp = client.get(f"/databases/{database_id}", params={"include_password": include_password})
    data = resp.get("data", {})

    if fmt == "json":
        print_json(data)
        return
    if fmt == "env":
        _print_get_env(data, show_password)
        return
    _print_get_table(data, show_password)


@app.command("create")
def create_database(
    engine: str = typer.Option(..., "--engine", "-e", help="Database engine: postgres, mysql, or redis."),
    size: str = typer.Option(..., "--size", "-s", help="Node size: xs, sm, md, lg, xl."),
    count: int = typer.Option(1, "--count", "-n", help="Node count (1-3)."),
    postgres_version: str = typer.Option("16", "--postgres-version", help="Postgres version: 14, 15, 16."),
    mysql_version: str = typer.Option("8.0", "--mysql-version", help="MySQL version: 8.0."),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait until the database becomes ready."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Create a new database."""
    if engine not in ("postgres", "mysql", "redis"):
        typer.echo("Error: --engine must be 'postgres', 'mysql', or 'redis'", err=True)
        raise typer.Exit(code=1)
    if size not in ("xs", "sm", "md", "lg", "xl"):
        typer.echo("Error: --size must be one of: xs, sm, md, lg, xl", err=True)
        raise typer.Exit(code=1)

    client = get_client(project)
    payload: dict[str, Any] = {
        "engine": engine,
        "node_type": size,
        "node_count": count,
    }
    if engine == "postgres":
        payload["postgres_version"] = postgres_version
    elif engine == "mysql":
        payload["mysql_version"] = mysql_version
    resp = client.post("/databases", data=payload)
    data = resp.get("data", {})

    if json:
        if wait and data.get("id"):
            final_status = _wait_for_database(client, str(data["id"]))
            if final_status == "created":
                detail = client.get(
                    f"/databases/{data['id']}", params={"include_password": "true"}
                ).get("data", {})
                data = detail
            else:
                data = {**data, "status": final_status}
        print_json(data)
        return

    name = data.get("name", "")
    database_id = data.get("id", "")
    typer.echo(f"Database created: {name} ({database_id})")

    if not wait:
        typer.echo(f"Status: {data.get('status')}")
        return

    if not database_id:
        typer.echo("Cannot wait: server did not return a database id.", err=True)
        raise typer.Exit(code=1)

    try:
        final_status = _wait_for_database(client, str(database_id))
    except KeyboardInterrupt:
        typer.echo("\nStopped waiting. The database is still being provisioned in the background.")
        raise typer.Exit(code=130)

    if final_status != "created":
        typer.echo(f"Database failed to become ready (status: {final_status}).", err=True)
        raise typer.Exit(code=1)

    typer.echo("Database is ready.")
    detail = client.get(
        f"/databases/{database_id}", params={"include_password": "true"}
    ).get("data", {})
    _print_connection_table(detail.get("connection") or {}, show_password=True)


@app.command("delete")
def delete_database(
    database_id: str = typer.Argument(help="Database ID."),
    force: bool = typer.Option(False, "--force", help="Skip the typed confirmation."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Delete a database."""
    _confirm_delete(
        f"This will permanently delete database {database_id}.",
        force=force,
    )
    client = get_client(project)
    client.delete(f"/databases/{database_id}")
    typer.echo("Database deleted.")


@app.command("resize")
def resize_database(
    database_id: str = typer.Argument(help="Database ID."),
    size: str | None = typer.Option(None, "--size", "-s", help="New node size: xs, sm, md, lg, xl."),
    count: int | None = typer.Option(None, "--count", "-n", help="New node count (1-3)."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Resize a database (change node size and/or count)."""
    if size is None and count is None:
        typer.echo("Error: provide --size and/or --count", err=True)
        raise typer.Exit(code=1)
    if size is not None and size not in ("xs", "sm", "md", "lg", "xl"):
        typer.echo("Error: --size must be one of: xs, sm, md, lg, xl", err=True)
        raise typer.Exit(code=1)

    payload: dict[str, Any] = {}
    if size is not None:
        payload["node_type"] = size
    if count is not None:
        payload["node_count"] = count

    client = get_client(project)
    resp = client.patch(f"/databases/{database_id}", data=payload)
    data = resp.get("data", {})

    if json:
        print_json(data)
        return
    typer.echo(f"Resize scheduled. Current status: {data.get('status', 'unknown')}")


@app.command("allow-apps")
def allow_apps(
    database_id: str = typer.Argument(help="Database ID."),
    apps: list[str] = typer.Option(
        [], "--app", "-a",
        help="App UUID to allow. Pass multiple times. Empty list (no --app) clears the allowlist.",
    ),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Set the list of apps allowed to access this database."""
    client = get_client(project)
    client.patch(
        f"/databases/{database_id}",
        data={"allowed_inbound_sources": apps},
    )
    if apps:
        typer.echo(f"Allowed apps updated ({len(apps)} app(s)).")
    else:
        typer.echo("Allowlist cleared.")


# ── replica ──────────────────────────────────────────────────────────────


@replica_app.command("create")
def replica_create(
    database_id: str = typer.Argument(help="Database ID."),
    size: str = typer.Option(..., "--size", "-s", help="Replica size: xs, sm, md, lg, xl."),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait until the replica becomes ready."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Create a read-only replica."""
    if size not in ("xs", "sm", "md", "lg", "xl"):
        typer.echo("Error: --size must be one of: xs, sm, md, lg, xl", err=True)
        raise typer.Exit(code=1)
    client = get_client(project)
    resp = client.post(f"/databases/{database_id}/replica", data={"size": size})
    data = resp.get("data", {})

    if json:
        if wait:
            replica = _wait_for_replica(client, database_id)
            if replica.get("status") == "available":
                detail = client.get(
                    f"/databases/{database_id}", params={"include_password": "true"}
                ).get("data", {})
                data = detail.get("readonly_replica") or replica
            else:
                data = replica
        print_json(data)
        return

    typer.echo(f"Replica created: id={data.get('id')} size={data.get('size')} status={data.get('status')}")

    if not wait:
        return

    try:
        replica = _wait_for_replica(client, database_id)
    except KeyboardInterrupt:
        typer.echo("\nStopped waiting. The replica is still being provisioned in the background.")
        raise typer.Exit(code=130)

    if replica.get("status") != "available":
        typer.echo(f"Replica failed to become ready (status: {replica.get('status')}).", err=True)
        raise typer.Exit(code=1)

    typer.echo("Replica is ready.")
    detail = client.get(
        f"/databases/{database_id}", params={"include_password": "true"}
    ).get("data", {})
    replica_full = detail.get("readonly_replica") or {}
    _print_connection_table(replica_full.get("connection") or {}, show_password=True)


@replica_app.command("resize")
def replica_resize(
    database_id: str = typer.Argument(help="Database ID."),
    size: str = typer.Option(..., "--size", "-s", help="New replica size: xs, sm, md, lg, xl."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Resize the read-only replica."""
    if size not in ("xs", "sm", "md", "lg", "xl"):
        typer.echo("Error: --size must be one of: xs, sm, md, lg, xl", err=True)
        raise typer.Exit(code=1)
    client = get_client(project)
    resp = client.patch(f"/databases/{database_id}/replica", data={"size": size})
    data = resp.get("data", {})
    if json:
        print_json(data)
        return
    typer.echo(f"Replica resize scheduled: size={data.get('size')}")


@replica_app.command("delete")
def replica_delete(
    database_id: str = typer.Argument(help="Database ID."),
    force: bool = typer.Option(False, "--force", help="Skip the typed confirmation."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Delete the read-only replica."""
    _confirm_delete(
        f"This will permanently delete the replica of database {database_id}.",
        force=force,
    )
    client = get_client(project)
    client.delete(f"/databases/{database_id}/replica")
    typer.echo("Replica deleted.")


# ── backups ──────────────────────────────────────────────────────────────


@backups_app.command("list")
def backups_list(
    database_id: str = typer.Argument(help="Database ID."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """List backups."""
    client = get_client(project)
    resp = client.get(f"/databases/{database_id}/backups")
    data = resp.get("data", [])
    output(data, BACKUP_COLUMNS, title="Backups", as_json=json)


@backups_app.command("create")
def backups_create(
    database_id: str = typer.Argument(help="Database ID."),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait until the backup completes."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Create a new backup."""
    client = get_client(project)
    resp = client.post(f"/databases/{database_id}/backups")
    data = resp.get("data", {})
    backup_id = data.get("id", "")

    if json:
        if wait and backup_id:
            data = _wait_for_backup(client, database_id, str(backup_id)) or data
        print_json(data)
        return

    typer.echo(f"Backup scheduled: id={backup_id} name={data.get('name')} status={data.get('status')}")

    if not wait:
        return
    if not backup_id:
        typer.echo("Cannot wait: server did not return a backup id.", err=True)
        raise typer.Exit(code=1)

    try:
        final = _wait_for_backup(client, database_id, str(backup_id))
    except KeyboardInterrupt:
        typer.echo("\nStopped waiting. The backup is still being created in the background.")
        raise typer.Exit(code=130)

    status = final.get("status")
    if status != "completed":
        reason = final.get("reason") or "unknown reason"
        typer.echo(f"Backup failed (status: {status}): {reason}", err=True)
        raise typer.Exit(code=1)

    table = Table(title="Backup")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Name", str(final.get("name") or ""))
    table.add_row("Created At", str(final.get("created_at") or ""))
    table.add_row("Size", _format_size(final.get("size")))
    console.print(table)


@backups_app.command("delete")
def backups_delete(
    database_id: str = typer.Argument(help="Database ID."),
    backup_id: str = typer.Argument(help="Backup ID."),
    force: bool = typer.Option(False, "--force", help="Skip the typed confirmation."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Delete a backup."""
    _confirm_delete(
        f"This will permanently delete backup {backup_id}.",
        force=force,
    )
    client = get_client(project)
    client.delete(f"/databases/{database_id}/backups/{backup_id}")
    typer.echo("Backup deleted.")


# ── pool ─────────────────────────────────────────────────────────────────


@pool_app.command("list")
def pool_list(
    database_id: str = typer.Argument(help="Database ID."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """List connection pools."""
    client = get_client(project)
    resp = client.get(f"/databases/{database_id}/connection-pools")
    data = resp.get("data", [])
    output(data, POOL_COLUMNS, title="Connection Pools", as_json=json)


@pool_app.command("create")
def pool_create(
    database_id: str = typer.Argument(help="Database ID."),
    size: int = typer.Option(..., "--size", "-s", help="Pool size (max connections)."),
    mode: str = typer.Option("transaction", "--mode", "-m", help="Pool mode: transaction or session."),
    target: str = typer.Option("primary", "--target", "-t", help="Pool target: primary or replica."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Create a connection pool."""
    if mode not in ("transaction", "session"):
        typer.echo("Error: --mode must be 'transaction' or 'session'", err=True)
        raise typer.Exit(code=1)
    if target not in ("primary", "replica"):
        typer.echo("Error: --target must be 'primary' or 'replica'", err=True)
        raise typer.Exit(code=1)

    client = get_client(project)
    resp = client.post(
        f"/databases/{database_id}/connection-pools",
        data={"size": size, "mode": mode, "target": target},
    )
    data = resp.get("data", {})
    if json:
        print_json(data)
        return
    typer.echo(
        f"Pool created: id={data.get('id')} name={data.get('name')} "
        f"target={data.get('target')} mode={data.get('mode')} size={data.get('size')} "
        f"status={data.get('status')}"
    )


@pool_app.command("update")
def pool_update(
    database_id: str = typer.Argument(help="Database ID."),
    pool_id: str = typer.Argument(help="Pool ID."),
    size: int | None = typer.Option(None, "--size", "-s", help="New pool size."),
    mode: str | None = typer.Option(None, "--mode", "-m", help="New pool mode: transaction or session."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Update a connection pool."""
    if size is None and mode is None:
        typer.echo("Error: provide --size and/or --mode", err=True)
        raise typer.Exit(code=1)
    if mode is not None and mode not in ("transaction", "session"):
        typer.echo("Error: --mode must be 'transaction' or 'session'", err=True)
        raise typer.Exit(code=1)

    payload: dict[str, Any] = {}
    if size is not None:
        payload["size"] = size
    if mode is not None:
        payload["mode"] = mode

    client = get_client(project)
    resp = client.patch(f"/databases/{database_id}/connection-pools/{pool_id}", data=payload)
    data = resp.get("data", {})
    if json:
        print_json(data)
        return
    typer.echo(
        f"Pool updated: id={data.get('id')} mode={data.get('mode')} "
        f"size={data.get('size')} status={data.get('status')}"
    )


@pool_app.command("delete")
def pool_delete(
    database_id: str = typer.Argument(help="Database ID."),
    pool_id: str = typer.Argument(help="Pool ID."),
    force: bool = typer.Option(False, "--force", help="Skip the typed confirmation."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Delete a connection pool."""
    _confirm_delete(
        f"This will permanently delete connection pool {pool_id}.",
        force=force,
    )
    client = get_client(project)
    client.delete(f"/databases/{database_id}/connection-pools/{pool_id}")
    typer.echo("Pool deleted.")


# ── db (logical databases inside an instance) ───────────────────────────


def _fetch_entries(client, database_id: str) -> dict[str, Any]:
    return client.get(f"/databases/{database_id}/entries").get("data", {})


@db_app.command("list")
def db_list(
    database_id: str = typer.Argument(help="Database ID."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """List logical databases inside an instance."""
    client = get_client(project)
    data = _fetch_entries(client, database_id)
    dbs = data.get("databases", [])
    output(dbs, PGDB_COLUMNS, title="Databases", as_json=json)


@db_app.command("create")
def db_create(
    database_id: str = typer.Argument(help="Database ID."),
    name: str = typer.Option(..., "--name", "-n", help="New database name."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Create a logical database inside an instance."""
    client = get_client(project)
    resp = client.post(f"/databases/{database_id}/databases", data={"name": name})
    data = resp.get("data", {})
    if json:
        print_json(data)
        return
    typer.echo(f"Database created: id={data.get('id')} name={data.get('name')}")


@db_app.command("delete")
def db_delete(
    database_id: str = typer.Argument(help="Database ID."),
    entry_id: str = typer.Argument(help="Database entry ID."),
    force: bool = typer.Option(False, "--force", help="Skip the typed confirmation."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Delete a logical database inside an instance."""
    _confirm_delete(
        f"This will permanently delete database {entry_id}.",
        force=force,
    )
    client = get_client(project)
    client.delete(f"/databases/{database_id}/databases/{entry_id}")
    typer.echo("Database deleted.")


# ── user (users inside an instance) ─────────────────────────────────────


def _format_grants(permissions: list[dict[str, Any]]) -> str:
    if not permissions:
        return ""
    return ", ".join(f"{p.get('database')}={p.get('permissions')}" for p in permissions)


@user_app.command("list")
def user_list(
    database_id: str = typer.Argument(help="Database ID."),
    show_password: bool = typer.Option(False, "--show-password", help="Show user passwords in clear."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """List users inside an instance."""
    client = get_client(project)
    data = _fetch_entries(client, database_id)
    users = data.get("users", [])

    if json:
        print_json(users)
        return

    table = Table(title="Users")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Password")
    table.add_column("Grants")
    table.add_column("Created At")
    for user in users:
        password = user.get("password")
        password_cell = password if (show_password and password) else "<hidden>"
        table.add_row(
            str(user.get("id", "")),
            user.get("name", ""),
            password_cell,
            _format_grants(user.get("permissions", [])),
            str(user.get("created_at", "")),
        )
    console.print(table)


def _parse_grant(value: str) -> dict[str, str]:
    if "=" not in value:
        raise typer.BadParameter(f"Invalid grant '{value}'. Expected format: <dbname>=ro|all|null")
    dbname, perm = value.split("=", 1)
    dbname = dbname.strip()
    perm = perm.strip()
    if not dbname:
        raise typer.BadParameter(f"Invalid grant '{value}'. Database name is empty")
    if perm not in ("ro", "all", "null"):
        raise typer.BadParameter(f"Invalid grant permission '{perm}'. Must be ro, all, or null")
    return {"database": dbname, "permissions": perm}


@user_app.command("create")
def user_create(
    database_id: str = typer.Argument(help="Database ID."),
    name: str = typer.Option(..., "--name", "-n", help="New user name."),
    grants: list[str] = typer.Option(
        [], "--grant", "-g",
        help="Grant in form <dbname>=ro|all|null. Pass multiple times.",
    ),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Create a user (password is generated and shown in the output)."""
    permissions = [_parse_grant(g) for g in grants]
    client = get_client(project)
    resp = client.post(
        f"/databases/{database_id}/users",
        data={"name": name, "permissions": permissions},
    )
    data = resp.get("data", {})
    if json:
        print_json(data)
        return

    typer.echo(f"User created: id={data.get('id')} name={data.get('name')}")
    if password := data.get("password"):
        typer.echo(f"Password: {password}")
    if perms := data.get("permissions"):
        typer.echo(f"Grants: {_format_grants(perms)}")


@user_app.command("delete")
def user_delete(
    database_id: str = typer.Argument(help="Database ID."),
    entry_id: str = typer.Argument(help="User entry ID."),
    force: bool = typer.Option(False, "--force", help="Skip the typed confirmation."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Delete a user."""
    _confirm_delete(
        f"This will permanently delete user {entry_id}.",
        force=force,
    )
    client = get_client(project)
    client.delete(f"/databases/{database_id}/users/{entry_id}")
    typer.echo("User deleted.")
