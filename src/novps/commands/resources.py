from __future__ import annotations

import asyncio
import os
import re
import select
import signal
import ssl
import sys
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import certifi
import httpx
import typer
import websockets
from rich.table import Table

from novps.client import get_client
from novps.config import get_ws_url
from novps.output import console, print_json

app = typer.Typer(no_args_is_help=True)

DURATION_PATTERN = re.compile(r"^(\d+)([smhd])$")
DURATION_MULTIPLIERS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_POLL_INTERVAL = 3


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


# ── logs helpers ──────────────────────────────────────────────────────


def _parse_since(value: str) -> int:
    match = DURATION_PATTERN.match(value)
    if not match:
        raise typer.BadParameter(
            f"Invalid duration '{value}'. Use format like 30s, 5m, 1h, 1d."
        )
    return int(match.group(1)) * DURATION_MULTIPLIERS[match.group(2)]


def _format_ts(ts_ns: str) -> str:
    dt = datetime.fromtimestamp(int(ts_ns) / 1e9, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _now_ns() -> str:
    return str(int(time.time() * 1e9))


def _flatten(result: list) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for stream in result:
        for ts_ns, line in stream.get("values", []):
            entries.append((ts_ns, line.rstrip("\n")))
    entries.sort(key=lambda e: int(e[0]))
    return entries


def _fetch_logs(
    client,
    resource_id: str,
    start_ns: str,
    end_ns: str,
    limit: int,
    direction: str,
    search: str | None,
    pod: str | None,
) -> list[tuple[str, str]]:
    params: dict[str, str | int] = {
        "start": start_ns,
        "end": end_ns,
        "limit": limit,
        "direction": direction,
    }
    if search:
        params["search"] = search
    if pod:
        params["pod"] = pod
    resp = client.get(f"/resources/{resource_id}/logs?{urlencode(params)}")
    data = resp.get("data", {})
    return _flatten(data.get("result", []))


@app.command("logs")
def resource_logs(
    resource_id: str = typer.Argument(help="Resource ID."),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output."),
    lines: int = typer.Option(
        100, "--lines", "-n", help="Number of log lines (1-5000)."
    ),
    since: str = typer.Option(
        "1h", "--since", "-s", help="Show logs since duration (e.g. 30s, 5m, 1h, 1d)."
    ),
    search: str | None = typer.Option(None, "--search", help="Filter by substring."),
    pod: str | None = typer.Option(None, "--pod", help="Filter by pod name."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """View resource logs."""
    since_seconds = _parse_since(since)
    client = get_client(project)

    end_ns = _now_ns()
    start_ns = str(int(end_ns) - since_seconds * 1_000_000_000)

    entries = _fetch_logs(
        client, resource_id, start_ns, end_ns, lines, "backward", search, pod
    )

    for ts_ns, line in entries:
        typer.echo(f"{_format_ts(ts_ns)}  {line}")

    if not follow:
        return

    cursor_ns = entries[-1][0] if entries else end_ns

    try:
        while True:
            time.sleep(_POLL_INTERVAL)
            new_start = str(int(cursor_ns) + 1)
            new_end = _now_ns()
            new_entries = _fetch_logs(
                client, resource_id, new_start, new_end, lines, "forward", search, pod
            )

            for ts_ns, line in new_entries:
                typer.echo(f"{_format_ts(ts_ns)}  {line}")

            if new_entries:
                cursor_ns = new_entries[-1][0]
    except KeyboardInterrupt:
        pass
    except (httpx.RemoteProtocolError, httpx.ConnectError):
        typer.echo("Session closed by server... Bye-bye")


# ── exec/connect helpers ─────────────────────────────────────────────


def _get_terminal_size() -> tuple[int, int]:
    try:
        cols, rows = os.get_terminal_size()
        return cols, rows
    except OSError:
        return 80, 24


_RECV_TIMEOUT = 5  # seconds to wait for server output after Enter before assuming shell exited


def _read_stdin_loop(
    fd: int, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop,
    stop: threading.Event,
) -> None:
    """Read stdin in a thread using select() with timeout so we can check stop flag."""
    while not stop.is_set():
        ready, _, _ = select.select([fd], [], [], 0.1)
        if not ready:
            continue
        try:
            data = os.read(fd, 4096)
        except OSError:
            break
        if not data:
            break
        loop.call_soon_threadsafe(queue.put_nowait, data)
    loop.call_soon_threadsafe(queue.put_nowait, None)


async def _async_connect(ws_base: str, websocket_path: str) -> None:
    ws_url = ws_base + websocket_path
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())

    loop = asyncio.get_event_loop()
    stdin_fd = sys.stdin.fileno()
    stop = threading.Event()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    stdin_thread = threading.Thread(
        target=_read_stdin_loop, args=(stdin_fd, queue, loop, stop), daemon=True,
    )
    stdin_thread.start()

    ws = await websockets.connect(ws_url, ssl=ssl_ctx, close_timeout=1)

    try:
        # Send initial terminal size
        cols, rows = _get_terminal_size()
        await ws.send(f"resize:{cols}:{rows}")

        # Handle SIGWINCH (terminal resize)
        if sys.platform != "win32":
            def on_resize() -> None:
                c, r = _get_terminal_size()
                asyncio.ensure_future(ws.send(f"resize:{c}:{r}"))

            loop.add_signal_handler(signal.SIGWINCH, on_resize)

        stdin_task = asyncio.create_task(_stdin_to_ws(queue, ws))
        stdout_task = asyncio.create_task(_ws_to_stdout(ws))

        done, pending = await asyncio.wait(
            [stdin_task, stdout_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        queue.put_nowait(None)

        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
    except (websockets.exceptions.ConnectionClosedOK, websockets.exceptions.ConnectionClosedError):
        pass
    finally:
        stop.set()
        try:
            await asyncio.wait_for(ws.close(), timeout=1)
        except Exception:
            pass
        if sys.platform != "win32":
            loop.remove_signal_handler(signal.SIGWINCH)


async def _stdin_to_ws(queue: asyncio.Queue, ws: websockets.ClientConnection) -> None:
    try:
        while True:
            data = await queue.get()
            if data is None:
                return
            # Ctrl+] — local disconnect (like telnet)
            if b"\x1d" in data:
                return
            await ws.send(data.decode("utf-8", errors="replace"))
    except websockets.exceptions.ConnectionClosed:
        pass


async def _ws_to_stdout(ws: websockets.ClientConnection) -> None:
    """Receive WS messages and write to stdout.

    Uses a recv timeout: after _RECV_TIMEOUT seconds of silence, sends a
    probe newline. If still no response within _RECV_TIMEOUT seconds, assumes
    the remote shell has exited and returns.
    """
    try:
        while True:
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT)
            except asyncio.TimeoutError:
                # No output for a while — probe to see if the shell is alive
                try:
                    await ws.send("\n")
                except websockets.exceptions.ConnectionClosed:
                    return
                # Wait for response to probe
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT)
                except asyncio.TimeoutError:
                    # Shell is dead — no response to probe
                    return

            if isinstance(message, str):
                sys.stdout.write(message)
                sys.stdout.flush()
            elif isinstance(message, bytes):
                sys.stdout.buffer.write(message)
                sys.stdout.buffer.flush()
    except websockets.exceptions.ConnectionClosed:
        pass


@app.command("connect")
def resource_connect(
    resource_id: str = typer.Argument(help="Resource ID to connect to."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Connect to a resource pod for interactive shell access."""
    client = get_client(project)

    resp = client.post("/exec/ticket", data={"resource_id": resource_id})
    data = resp.get("data", {})

    ticket = data.get("ticket")
    websocket_path = data.get("websocket_path")

    if not ticket or not websocket_path:
        typer.echo("Error: Failed to obtain exec ticket.", err=True)
        raise typer.Exit(code=1)

    ws_base = get_ws_url()

    typer.echo("Use Ctrl+] to disconnect.\n")

    # Put terminal in raw mode for interactive shell
    if sys.stdin.isatty():
        import termios
        import tty

        old_settings = termios.tcgetattr(sys.stdin)
        try:
            tty.setraw(sys.stdin.fileno())
            asyncio.run(_async_connect(ws_base, websocket_path))
        except KeyboardInterrupt:
            pass
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            typer.echo("\nConnection closed.")
    else:
        try:
            asyncio.run(_async_connect(ws_base, websocket_path))
        except KeyboardInterrupt:
            pass


def _parse_replicas(value: str) -> tuple[str, int]:
    if ":" not in value:
        raise typer.BadParameter("Expected format SIZE:COUNT (e.g. sm:2)")
    size, count_s = value.split(":", 1)
    if size not in ("xs", "sm", "md", "lg", "xl"):
        raise typer.BadParameter("SIZE must be one of: xs, sm, md, lg, xl")
    try:
        count = int(count_s)
    except ValueError:
        raise typer.BadParameter("COUNT must be an integer")
    if not (1 <= count <= 10):
        raise typer.BadParameter("COUNT must be between 1 and 10")
    return size, count


def _parse_env_pair(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise typer.BadParameter(f"Expected KEY=VALUE, got: {value}")
    key, val = value.split("=", 1)
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", key):
        raise typer.BadParameter(f"Invalid env key: {key}")
    return key, val


def _confirm_delete(message: str, *, force: bool) -> None:
    if force:
        return
    typer.echo(message)
    answer = typer.prompt("Type DELETE to confirm", default="", show_default=False)
    if answer != "DELETE":
        typer.echo("Aborted.")
        raise typer.Exit(code=1)


@app.command("update")
def update_resource(
    resource_id: str = typer.Argument(help="Resource ID."),
    image: str | None = typer.Option(None, "--image", help="New image name."),
    tag: str | None = typer.Option(None, "--tag", help="New image tag."),
    replicas: str | None = typer.Option(None, "--replicas", help="Replica size:count, e.g. sm:2."),
    command: str | None = typer.Option(None, "--command", help="Override command."),
    port: str | None = typer.Option(None, "--port", help="HTTP port."),
    schedule: str | None = typer.Option(None, "--schedule", help="Cron schedule (for cron-job)."),
    env: list[str] = typer.Option([], "--env", "-e", help="KEY=VALUE env var (repeatable)."),
    no_deploy: bool = typer.Option(False, "--no-deploy", help="Do not trigger a deployment."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Update a resource's configuration."""
    payload: dict = {}
    if image is not None:
        payload["image_name"] = image
    if tag is not None:
        payload["image_tag"] = tag
    if replicas is not None:
        size, count = _parse_replicas(replicas)
        payload["replicas_type"] = size
        payload["replicas_count"] = count
    if command is not None:
        payload["command"] = command
    if port is not None:
        payload["port"] = port
    if schedule is not None:
        payload["schedule"] = schedule
    if env:
        payload["envs"] = [{"key": k, "value": v} for k, v in (_parse_env_pair(e) for e in env)]
    if no_deploy:
        payload["do_not_deploy"] = True

    if not payload:
        typer.echo("Nothing to update. Provide at least one flag.", err=True)
        raise typer.Exit(code=1)

    client = get_client(project)
    resp = client.patch(f"/resources/{resource_id}", data=payload)
    data = resp.get("data", {})
    if json:
        print_json(data)
        return
    updated = data.get("updated", False)
    typer.echo(f"Resource {resource_id}: {'updated' if updated else 'no changes'}")


@app.command("scale")
def scale_resource(
    resource_id: str = typer.Argument(help="Resource ID."),
    replicas: str = typer.Option(..., "--replicas", "-r", help="Replica size:count, e.g. sm:2."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Scale a resource (shortcut for update --replicas)."""
    size, count = _parse_replicas(replicas)
    client = get_client(project)
    client.patch(f"/resources/{resource_id}", data={"replicas_type": size, "replicas_count": count})
    typer.echo(f"Resource {resource_id} scaled to {size}:{count}")


@app.command("set-image")
def set_image(
    resource_id: str = typer.Argument(help="Resource ID."),
    image: str | None = typer.Option(None, "--image", help="New image name."),
    tag: str | None = typer.Option(None, "--tag", help="New image tag."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Update the docker image and/or tag (shortcut for update)."""
    payload: dict = {}
    if image is not None:
        payload["image_name"] = image
    if tag is not None:
        payload["image_tag"] = tag
    if not payload:
        typer.echo("Provide --image and/or --tag.", err=True)
        raise typer.Exit(code=1)
    client = get_client(project)
    client.patch(f"/resources/{resource_id}", data=payload)
    typer.echo(f"Resource {resource_id}: image updated.")


@app.command("set-env")
def set_env(
    resource_id: str = typer.Argument(help="Resource ID."),
    pairs: list[str] = typer.Argument(..., help="KEY=VALUE pairs (repeatable)."),
    merge: bool = typer.Option(True, "--merge/--replace", help="Merge with existing envs or replace."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Set resource environment variables."""
    new_pairs = [_parse_env_pair(p) for p in pairs]
    client = get_client(project)
    envs: list[dict] = []
    if merge:
        resp = client.get(f"/resources/{resource_id}/environment-variables")
        for e in resp.get("data", []) or []:
            envs.append({"key": e.get("key"), "value": e.get("value", "")})
        new_keys = {k for k, _ in new_pairs}
        envs = [e for e in envs if e["key"] not in new_keys]
    for k, v in new_pairs:
        envs.append({"key": k, "value": v})
    client.patch(f"/resources/{resource_id}", data={"envs": envs})
    typer.echo(f"Resource {resource_id}: envs updated ({len(new_pairs)} changed).")


@app.command("delete")
def delete_resource(
    resource_id: str = typer.Argument(help="Resource ID."),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Delete a resource (soft delete)."""
    _confirm_delete(f"This will delete resource {resource_id}.", force=force)
    client = get_client(project)
    client.delete(f"/resources/{resource_id}")
    typer.echo(f"Resource {resource_id} deleted.")


@app.command("deploy")
def deploy_resource(
    resource_id: str = typer.Argument(help="Resource ID."),
    json: bool = typer.Option(False, "--json", help="Output as JSON."),
    project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Trigger a manual deployment for the resource."""
    client = get_client(project)
    resp = client.post(f"/resources/{resource_id}/deployment", data={})
    data = resp.get("data", {})
    if json:
        print_json(data)
        return
    typer.echo(f"Deployment queued: {data.get('id')} (status: {data.get('status')})")
