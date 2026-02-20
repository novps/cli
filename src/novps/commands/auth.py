from __future__ import annotations

import typer

from novps.client import NoVPSClient
from novps.config import get_api_url, get_token, load_config, save_config

app = typer.Typer(no_args_is_help=True)


@app.command()
def login(
    project: str = typer.Option("default", "--project", "-p", help="Project alias to store the token under."),
) -> None:
    """Authenticate with a Personal Access Token."""
    token = typer.prompt("Enter your Personal Access Token", hide_input=True)

    if not token.startswith("nvps_"):
        typer.echo("Error: Invalid token format. Token must start with 'nvps_'.", err=True)
        raise typer.Exit(code=1)

    # Validate token by calling the API
    client = NoVPSClient(token=token, base_url=get_api_url())
    try:
        client.get("/apps")
    except SystemExit:
        typer.echo("Error: Token validation failed.", err=True)
        raise typer.Exit(code=1)

    config = load_config()
    config.setdefault("projects", {}).setdefault(project, {})["token"] = token
    save_config(config)
    typer.echo(f"Authenticated successfully (project: {project}).")


@app.command()
def logout(
    project: str = typer.Option("default", "--project", "-p", help="Project alias to remove the token for."),
) -> None:
    """Remove saved authentication token."""
    config = load_config()
    projects = config.get("projects", {})
    if project not in projects or "token" not in projects.get(project, {}):
        typer.echo(f"Not currently authenticated for project '{project}'.")
        return
    del projects[project]["token"]
    if not projects[project]:
        del projects[project]
    save_config(config)
    typer.echo(f"Logged out successfully (project: {project}).")


@app.command()
def status(
    project: str = typer.Option("default", "--project", "-p", help="Project alias to check."),
) -> None:
    """Show current authentication status."""
    token = get_token(project)
    if not token:
        typer.echo(f"Not authenticated for project '{project}'. Run 'novps auth login --project={project}' to authenticate.")
        return

    prefix = token[:13] if len(token) >= 13 else token  # "nvps_" + 8 chars
    typer.echo(f"Authenticated (project: {project}, token: {prefix}...)")
