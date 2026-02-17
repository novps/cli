from __future__ import annotations

import typer

from novps.client import NoVPSClient
from novps.config import get_api_url, get_token, load_config, save_config

app = typer.Typer(no_args_is_help=True)


@app.command()
def login() -> None:
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
    config["token"] = token
    save_config(config)
    typer.echo("Authenticated successfully.")


@app.command()
def logout() -> None:
    """Remove saved authentication token."""
    config = load_config()
    if "token" not in config:
        typer.echo("Not currently authenticated.")
        return
    del config["token"]
    save_config(config)
    typer.echo("Logged out successfully.")


@app.command()
def status() -> None:
    """Show current authentication status."""
    token = get_token()
    if not token:
        typer.echo("Not authenticated. Run 'novps auth login' to authenticate.")
        return

    prefix = token[:13] if len(token) >= 13 else token  # "nvps_" + 8 chars
    typer.echo(f"Authenticated (token: {prefix}...)")
