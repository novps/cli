from __future__ import annotations

import asyncio
from typing import Optional

import typer
import websockets
from websockets.frames import CloseCode

from novps.client import get_client
from novps.config import get_ws_url

app = typer.Typer(no_args_is_help=True)

WS_CLOSE_MESSAGES: dict[int, str] = {
    CloseCode.NORMAL_CLOSURE: "Connection closed normally.",
    CloseCode.GOING_AWAY: "Remote server is going away.",
    CloseCode.POLICY_VIOLATION: "Policy violation — access denied.",
    CloseCode.INTERNAL_ERROR: "Remote server encountered an internal error.",
}


@app.command("resource")
def forward_resource(
    resource_id: str = typer.Argument(help="Resource ID to forward to."),
    remote_port: int = typer.Argument(help="Remote port on the resource."),
    local_port: Optional[int] = typer.Option(None, "--local-port", "-l", help="Local port to listen on (defaults to remote port)."),
) -> None:
    """Forward a local port to a resource."""
    _run_port_forward("resource", resource_id, remote_port, local_port or remote_port)


@app.command("database")
def forward_database(
    database_id: str = typer.Argument(help="Database ID to forward to."),
    local_port: Optional[int] = typer.Option(None, "--local-port", "-l", help="Local port to listen on (defaults to the database engine port)."),
) -> None:
    """Forward a local port to a database."""
    _run_port_forward("database", database_id, remote_port=None, local_port=local_port)


def _obtain_ticket(target_type: str, target_id: str, remote_port: int | None) -> dict:
    client = get_client()
    payload: dict = {"target_type": target_type, "target_id": target_id}
    if remote_port is not None:
        payload["port"] = remote_port
    resp = client.post("/port-forward/ticket", data=payload)
    return resp.get("data", {})


def _run_port_forward(
    target_type: str,
    target_id: str,
    remote_port: int | None,
    local_port: int | None,
) -> None:
    if local_port is None:
        # Request a ticket to determine the port from the server (databases)
        data = _obtain_ticket(target_type, target_id, remote_port)
        local_port = data.get("port", remote_port)
        if local_port is None:
            typer.echo("Error: could not determine local port.", err=True)
            raise typer.Exit(code=1)

    ws_base = get_ws_url()

    target_label = f"{target_type} {target_id}"
    if remote_port is not None:
        target_label += f":{remote_port}"

    typer.echo(f"Forwarding localhost:{local_port} → {target_label}...")
    typer.echo("Press Ctrl+C to stop.\n")

    try:
        asyncio.run(_async_forward(ws_base, target_type, target_id, remote_port, local_port))
    except KeyboardInterrupt:
        typer.echo("\nStopped.")


async def _async_forward(
    ws_base: str,
    target_type: str,
    target_id: str,
    remote_port: int | None,
    local_port: int,
) -> None:
    server = await asyncio.start_server(
        lambda r, w: _handle_tcp_connection(r, w, ws_base, target_type, target_id, remote_port),
        host="127.0.0.1",
        port=local_port,
    )
    await server.start_serving()
    await asyncio.Future()  # blocks until cancelled by SIGINT


async def _handle_tcp_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    ws_base: str,
    target_type: str,
    target_id: str,
    remote_port: int | None,
) -> None:
    peer = writer.get_extra_info("peername")
    typer.echo(f"[connect] {peer[0]}:{peer[1]}")

    try:
        data = await asyncio.to_thread(_obtain_ticket, target_type, target_id, remote_port)
        ticket = data["ticket"]
        ws_url = ws_base + data["websocket_path"]

        async with websockets.connect(
            ws_url,
            additional_headers={"X-Ticket": ticket},
        ) as ws:
            tcp_to_ws = asyncio.create_task(_tcp_to_ws(reader, ws))
            ws_to_tcp = asyncio.create_task(_ws_to_tcp(ws, writer))

            done, pending = await asyncio.wait(
                [tcp_to_ws, ws_to_tcp],
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

            for task in done:
                if task.exception() is not None:
                    exc = task.exception()
                    typer.echo(f"[error] {exc}", err=True)

    except websockets.exceptions.InvalidStatus as exc:
        typer.echo(f"[error] WebSocket rejected: HTTP {exc.response.status_code}", err=True)
    except websockets.exceptions.ConnectionClosed as exc:
        code = exc.rcvd.code if exc.rcvd else None
        msg = WS_CLOSE_MESSAGES.get(code, f"WebSocket closed with code {code}.")
        typer.echo(f"[disconnect] {msg}")
    except OSError as exc:
        typer.echo(f"[error] {exc}", err=True)
    finally:
        writer.close()
        await writer.wait_closed()
        typer.echo(f"[disconnect] {peer[0]}:{peer[1]}")


async def _tcp_to_ws(reader: asyncio.StreamReader, ws: websockets.ClientConnection) -> None:
    while True:
        data = await reader.read(65536)
        if not data:
            await ws.close()
            return
        await ws.send(data)


async def _ws_to_tcp(ws: websockets.ClientConnection, writer: asyncio.StreamWriter) -> None:
    async for message in ws:
        if isinstance(message, bytes):
            writer.write(message)
            await writer.drain()
