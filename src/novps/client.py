from __future__ import annotations

from typing import Any

import httpx
import typer

from novps.config import get_api_url, get_token


class NoVPSClient:
    def __init__(self, token: str, base_url: str) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": token},
            timeout=30.0,
        )

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, data: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, json=data)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.ConnectError:
            raise typer.Exit(
                code=1,
            ) from None

        if resp.status_code == 401:
            typer.echo("Error: Authentication failed. Run 'novps auth login' to re-authenticate.", err=True)
            raise typer.Exit(code=1)

        if resp.status_code >= 400:
            typer.echo(f"Error: API returned {resp.status_code}", err=True)
            try:
                body = resp.json()
                if errors := body.get("errors"):
                    for err in errors:
                        typer.echo(f"  - {err}", err=True)
            except Exception:
                pass
            raise typer.Exit(code=1)

        return resp.json()


def get_client() -> NoVPSClient:
    token = get_token()
    if not token:
        typer.echo("Error: Not authenticated. Run 'novps auth login' first.", err=True)
        raise typer.Exit(code=1)
    return NoVPSClient(token=token, base_url=get_api_url())
