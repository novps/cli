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

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, data: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, json=data)

    def patch(self, path: str, data: dict[str, Any] | None = None) -> Any:
        return self._request("PATCH", path, json=data)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)

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
            typer.echo(f"Error: API returned {resp.status_code} ({method} {self._client.base_url}{path})", err=True)
            try:
                body = resp.json()
                if errors := body.get("errors"):
                    if isinstance(errors, list):
                        for err in errors:
                            typer.echo(f"  - {err}", err=True)
                    else:
                        typer.echo(f"  - {errors}", err=True)
                elif detail := body.get("detail"):
                    if isinstance(detail, list):
                        for err in detail:
                            msg = err.get("msg") if isinstance(err, dict) else str(err)
                            typer.echo(f"  - {msg}", err=True)
                    else:
                        typer.echo(f"  - {detail}", err=True)
            except Exception:
                pass
            raise typer.Exit(code=1)

        if resp.status_code == 204 or not resp.content:
            return {"data": {}, "errors": None}

        return resp.json()


def get_client(project: str = "default") -> NoVPSClient:
    token = get_token(project)
    if not token:
        typer.echo(f"Error: Not authenticated for project '{project}'. Run 'novps auth login --project={project}' first.", err=True)
        raise typer.Exit(code=1)
    return NoVPSClient(token=token, base_url=get_api_url())
