from __future__ import annotations

from typing import Any

import httpx
import typer

from novps.config import get_api_url, get_token


def _format_validation_error(err: Any) -> str:
    """Render a FastAPI/Pydantic validation error with its `loc` path.

    Example: `body.resources.0.source.build_command: Field required`.
    """
    if not isinstance(err, dict):
        return str(err)
    loc_parts = err.get("loc") or []
    if loc_parts and loc_parts[0] == "body":
        loc_parts = loc_parts[1:]
    loc = ".".join(str(p) for p in loc_parts)
    msg = err.get("msg") or ""
    suffix = ""
    # pydantic's `missing` error sets `input` to the parent object — unhelpful to print.
    if err.get("type") != "missing":
        input_val = err.get("input")
        if isinstance(input_val, (str, int, float, bool)):
            shown = repr(input_val)
            if len(shown) <= 80:
                suffix = f" (got: {shown})"
    return f"{loc}: {msg}{suffix}" if loc else f"{msg}{suffix}"


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

    def put(self, path: str, data: dict[str, Any] | None = None) -> Any:
        return self._request("PUT", path, json=data)

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
                            typer.echo(f"  - {_format_validation_error(err)}", err=True)
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
