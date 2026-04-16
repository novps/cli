from __future__ import annotations

import sys
import time
from pathlib import Path

import typer
import yaml

from novps.client import get_client
from novps.manifest import ManifestError, load_manifest, resource_names
from novps.output import console, output, print_json

app = typer.Typer(no_args_is_help=True)

_DEPLOYMENT_TERMINAL_STATUSES = {"success", "failed", "canceled"}
_DEPLOYMENT_POLL_INTERVAL = 3
_DEPLOYMENT_POLL_TIMEOUT = 20 * 60

APP_COLUMNS = [
    ("id", "ID"),
    ("name", "Name"),
    ("resources_count", "Resources"),
    ("created_at", "Created At"),
]

RESOURCE_COLUMNS = [
    ("id", "ID"),
    ("name", "Name"),
    ("type", "Type"),
    ("public_domain", "Public Domain"),
    ("replicas_count", "Replicas"),
    ("schedule", "Schedule"),
]


@app.command("list")
def list_apps(
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """List applications."""
    client = get_client(project)
    resp = client.get("/apps")
    data = resp.get("data", [])
    output(data, APP_COLUMNS, title="Applications", as_json=json)


@app.command()
def resources(
    app_id: str = typer.Argument(help="Application ID."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """List resources for an application."""
    client = get_client(project)
    resp = client.get(f"/apps/{app_id}/resources")
    data = resp.get("data", [])
    output(data, RESOURCE_COLUMNS, title="Resources", as_json=json)


def _confirm_delete(message: str, *, force: bool) -> None:
    if force:
        return
    typer.echo(message)
    answer = typer.prompt("Type DELETE to confirm", default="", show_default=False)
    if answer != "DELETE":
        typer.echo("Aborted.")
        raise typer.Exit(code=1)


@app.command("update")
def update_app(
    app_id: str = typer.Argument(help="Application ID."),
    name: str | None = typer.Option(None, "--name", help="New application name."),
    description: str | None = typer.Option(None, "--description", help="New description."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Update application name or description."""
    payload: dict = {}
    if name is not None:
        payload["name"] = name
    if description is not None:
        payload["description"] = description
    if not payload:
        typer.echo("Nothing to update. Provide --name or --description.", err=True)
        raise typer.Exit(code=1)

    client = get_client(project)
    resp = client.patch(f"/apps/{app_id}", data=payload)
    data = resp.get("data", {})
    if json:
        print_json(data)
        return
    typer.echo(f"Application updated: {data.get('name')} ({data.get('id')})")


@app.command("delete")
def delete_app(
    app_id: str = typer.Argument(help="Application ID."),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Delete an application (soft delete)."""
    _confirm_delete(f"This will delete application {app_id} and all its resources.", force=force)
    client = get_client(project)
    client.delete(f"/apps/{app_id}")
    typer.echo(f"Application {app_id} deleted.")


@app.command("deploy")
def deploy_app(
    app_id: str = typer.Argument(help="Application ID."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Trigger a manual deployment for the application."""
    client = get_client(project)
    resp = client.post(f"/apps/{app_id}/deployment", data={})
    data = resp.get("data", {})
    if json:
        print_json(data)
        return
    typer.echo(f"Deployment queued: {data.get('id')} (status: {data.get('status')})")


def _has_github_source(manifest: dict) -> bool:
    for r in manifest.get("resources", []):
        if r.get("source_type") == "github":
            return True
    return False


def _ensure_github_connected(client) -> None:
    resp = client.get("/github/installations")
    installations = resp.get("data", [])
    if not installations:
        typer.echo(
            "GitHub is not connected to this project. "
            "Connect it in the web UI (Project → Settings → GitHub) and try again.",
            err=True,
        )
        raise typer.Exit(code=1)


def _wait_for_deployment(client, app_id: str, deployment_id: str) -> str:
    start = time.time()
    last_status = ""
    while time.time() - start < _DEPLOYMENT_POLL_TIMEOUT:
        resp = client.get(f"/apps/{app_id}/deployments/{deployment_id}")
        data = resp.get("data", {})
        status = data.get("status", "")
        if status != last_status:
            console.print(f"[dim]deployment status: {status}[/dim]")
            last_status = status
        if status in _DEPLOYMENT_TERMINAL_STATUSES:
            return status
        time.sleep(_DEPLOYMENT_POLL_INTERVAL)
    return last_status or "timeout"


def _collect_endpoints(client, resources_info: list[dict]) -> list[dict]:
    endpoints: list[dict] = []
    for r in resources_info:
        rid = r.get("id")
        if not rid or r.get("action") == "deleted":
            continue
        info = client.get(f"/resources/{rid}").get("data", {})
        endpoints.append({
            "name": info.get("name") or r.get("name"),
            "type": info.get("type"),
            "public_domain": info.get("public_domain"),
            "private_domain": info.get("private_domain"),
        })
    return endpoints


def _print_endpoints(endpoints: list[dict]) -> None:
    if not endpoints:
        return
    typer.echo("")
    typer.echo("Endpoints:")
    for e in endpoints:
        name = e.get("name") or ""
        kind = e.get("type") or ""
        public = e.get("public_domain")
        private = e.get("private_domain")
        typer.echo(f"  {name} ({kind}):")
        if kind == "web-app":
            typer.echo(f"    public:  https://{public}" if public else "    public:  (not ready)")
            typer.echo(f"    private: {private}" if private else "    private: (not ready)")
        else:
            typer.echo("    status: deployed")


@app.command("apply")
def apply_app(
    app_name: str = typer.Argument(help="Application name (unique per project)."),
    file: str = typer.Option(..., "--file", "-f", help="Path to the YAML manifest."),
    env_file: str | None = typer.Option(
        None, "--env-file", help="Path to a .env file (merged under shell env for ${VAR} substitution)."
    ),
    prune: bool = typer.Option(False, "--prune", help="Delete resources in the app that are not in the manifest."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Parse and validate only, do not call the API."),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for the deployment to finish."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Create or update an application from a YAML manifest."""
    try:
        manifest = load_manifest(file, env_file=env_file)
    except ManifestError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    if dry_run:
        payload = {"app_name": app_name, **manifest}
        print_json(payload) if json else typer.echo(f"Manifest OK. Resources: {', '.join(resource_names(manifest))}")
        return

    client = get_client(project)

    if _has_github_source(manifest):
        _ensure_github_connected(client)

    resp = client.put(f"/apps/{app_name}/apply", data=manifest)
    data = resp.get("data", {})
    app_info = data.get("app", {})
    app_id = app_info.get("id")
    deployment_id = data.get("deployment_id")
    resources_info = data.get("resources", [])

    if prune and app_id:
        manifest_names = set(resource_names(manifest))
        existing = client.get(f"/apps/{app_id}/resources").get("data", [])
        for r in existing:
            if r.get("name") not in manifest_names:
                rid = r.get("id")
                client.delete(f"/resources/{rid}")
                resources_info.append({"name": r.get("name"), "id": str(rid), "action": "deleted"})

    if not json:
        verb = "created" if app_info.get("created") else "updated"
        typer.echo(f"Application {verb}: {app_info.get('name')} ({app_id})")
        for r in resources_info:
            typer.echo(f"  - {r['name']}: {r['action']}")
        typer.echo(f"Deployment: {deployment_id}")

    endpoints: list[dict] | None = None
    deployment_status: str | None = None

    if wait and deployment_id and app_id:
        deployment_status = _wait_for_deployment(client, app_id, deployment_id)
        if deployment_status == "success":
            endpoints = _collect_endpoints(client, resources_info)

    if json:
        out = {**data, "resources": resources_info}
        if deployment_status is not None:
            out["deployment_status"] = deployment_status
        if endpoints is not None:
            out["endpoints"] = endpoints
        print_json(out)
    elif endpoints is not None:
        typer.echo("Deployment succeeded.")
        _print_endpoints(endpoints)

    if deployment_status is not None and deployment_status != "success":
        if not json:
            typer.echo(f"Deployment finished with status: {deployment_status}", err=True)
        raise typer.Exit(code=1)


@app.command("export")
def export_app(
    app_name: str = typer.Argument(help="Application name."),
    output_file: str | None = typer.Option(
        None, "--output", "-o", help="Write manifest to file instead of stdout."
    ),
    include_secrets: bool = typer.Option(
        False, "--include-secrets", help="Include env var values (requires apps.show-secrets permission)."
    ),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Export an existing application as a YAML manifest compatible with `apply`."""
    client = get_client(project)
    params = {"include_secrets": "true"} if include_secrets else None
    resp = client.get(f"/apps/{app_name}/export", params=params)
    data = resp.get("data", {})

    manifest = {"envs": data.get("envs", []), "resources": data.get("resources", [])}
    yaml_text = yaml.safe_dump(manifest, sort_keys=False, default_flow_style=False, allow_unicode=True)

    if output_file:
        Path(output_file).write_text(yaml_text)
        typer.echo(f"Exported to {output_file}", err=True)
    else:
        sys.stdout.write(yaml_text)
