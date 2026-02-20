from __future__ import annotations

import typer
from rich.table import Table

from novps.client import get_client
from novps.output import console, print_json

app = typer.Typer(no_args_is_help=True)


@app.command("get")
def resource_info(
    resource_id: str = typer.Argument(help="Resource ID."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Show detailed information for a resource."""
    client = get_client(project)
    resp = client.get(f"/resources/{resource_id}")
    data = resp.get("data", {})

    if json:
        print_json(data)
        return

    table = Table(title="Resource")
    table.add_column("Field", style="bold")
    table.add_column("Value")

    table.add_row("Name", data.get("name", ""))
    table.add_row("Type", data.get("type", ""))

    if data.get("public_domain") is not None:
        table.add_row("Public Domain", data["public_domain"])

    custom_domains = data.get("custom_domains")
    if custom_domains:
        lines = [f"{d['domain']} ({d['status']})" for d in custom_domains]
        table.add_row("Custom Domains", "\n".join(lines))

    if data.get("private_domain") is not None:
        table.add_row("Private Domain", data["private_domain"])

    if data.get("schedule") is not None:
        table.add_row("Schedule", data["schedule"])

    replica_size = data.get("replica_size")
    if replica_size:
        parts = [f"CPU: {replica_size['cpu']}", f"Memory: {replica_size['memory']}"]
        if replica_size.get("storage"):
            parts.append(f"Storage: {replica_size['storage']}")
        table.add_row("Replica Size", ", ".join(parts))

    table.add_row("Replicas", str(data.get("replicas_count", "")))
    table.add_row("Command", data.get("command") or "")

    if data.get("http_port") is not None:
        table.add_row("HTTP Port", str(data["http_port"]))

    internal_ports = data.get("internal_ports")
    if internal_ports:
        table.add_row("Internal Ports", ", ".join(str(p) for p in internal_ports))

    if data.get("docker_image"):
        image = data["docker_image"]
        if data.get("docker_tag"):
            image += f":{data['docker_tag']}"
        table.add_row("Docker Image", image)

    if data.get("docker_digest"):
        table.add_row("Docker Digest", data["docker_digest"])

    console.print(table)
