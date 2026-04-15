from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import typer
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeElapsedColumn, \
    TransferSpeedColumn
from rich.table import Table

from novps.client import get_client
from novps.output import console, output, print_json

app = typer.Typer(no_args_is_help=True)
files_app = typer.Typer(no_args_is_help=True, help="File operations within a bucket.")
keys_app = typer.Typer(no_args_is_help=True, help="Access key management.")

app.add_typer(files_app, name="files")
app.add_typer(keys_app, name="keys")

BUCKET_COLUMNS = [
    ("internal_domain", "Bucket"),
    ("name", "Name"),
    ("region", "Region"),
    ("access_level", "Access Level"),
    ("size", "Size"),
    ("objects", "Objects"),
    ("created_at", "Created At"),
]

FILE_COLUMNS = [
    ("type", "Type"),
    ("key", "Key"),
    ("size", "Size"),
    ("last_modified", "Last Modified"),
]

KEY_COLUMNS = [
    ("internal_name", "Key"),
    ("name", "Name"),
    ("key_id", "Access Key"),
    ("permissions_summary", "Permissions"),
    ("created_at", "Created At"),
]

ACCESS_LEVELS = ("private", "public-read", "public-full")
PERMISSION_LEVELS = ("ro", "rw")


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


def _confirm_delete(message: str, *, force: bool) -> None:
    if force:
        return
    typer.echo(message)
    typed = typer.prompt("Type DELETE to confirm", default="", show_default=False)
    if typed != "DELETE":
        typer.echo("Aborted.", err=True)
        raise typer.Exit(code=1)


def _parse_bucket_perm(value: str) -> tuple[str, str]:
    if ":" not in value:
        typer.echo(
            f"Error: --bucket must be in format <internal_domain>:<ro|rw>, got '{value}'",
            err=True,
        )
        raise typer.Exit(code=1)
    internal_domain, _, level = value.partition(":")
    internal_domain = internal_domain.strip()
    level = level.strip()
    if not internal_domain:
        typer.echo("Error: bucket is empty", err=True)
        raise typer.Exit(code=1)
    if level not in PERMISSION_LEVELS:
        typer.echo(
            f"Error: permission must be one of {', '.join(PERMISSION_LEVELS)}, got '{level}'",
            err=True,
        )
        raise typer.Exit(code=1)
    return internal_domain, level


def _format_bucket_row(bucket: dict[str, Any]) -> dict[str, Any]:
    return {
        **bucket,
        "size": _format_size(bucket.get("size")),
    }


def _format_file_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        **item,
        "size": _format_size(item.get("size")) if item.get("type") == "file" else "-",
        "last_modified": item.get("last_modified") or "-",
    }


def _format_key_row(key: dict[str, Any]) -> dict[str, Any]:
    perms = key.get("permissions") or []
    summary = ", ".join(f"{p.get('bucket')}:{p.get('permissions')}" for p in perms) or "-"
    return {**key, "permissions_summary": summary}


# ── buckets ───────────────────────────────────────────────────────────────


@app.command("list")
def list_buckets(
        json: bool = typer.Option(False, "--json", help="Output as JSON."),
        project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """List S3 buckets. 'Bucket' column is the identifier to pass to other commands."""
    client = get_client(project)
    resp = client.get("/storage")
    data = resp.get("data", [])
    if json:
        print_json(data)
        return
    output([_format_bucket_row(b) for b in data], BUCKET_COLUMNS, title="S3 Buckets")


@app.command("create")
def create_bucket(
        name: str = typer.Argument(help="Display name (3-40 chars, alphanumeric and dashes). A unique"
                                         " identifier suffix is appended automatically."),
        region: str = typer.Option("eu", "--region", help="Region."),
        json: bool = typer.Option(False, "--json", help="Output as JSON."),
        project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Create a new S3 bucket. The response's 'internal_domain' is the identifier to use."""
    client = get_client(project)
    resp = client.post("/storage", data={"name": name, "region": region})
    data = resp.get("data", {})
    if json:
        print_json(data)
        return
    typer.echo(
        f"Bucket created: name='{data.get('name')}' "
        f"identifier='{data.get('internal_domain')}' region={data.get('region')}"
    )


@app.command("delete")
def delete_bucket(
        bucket: str = typer.Argument(help="Bucket identifier (internal_domain)."),
        force: bool = typer.Option(False, "--force", help="Skip the typed confirmation."),
        project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Delete an S3 bucket (all objects are permanently removed)."""
    _confirm_delete(f"This will permanently delete bucket '{bucket}' and all its contents.", force=force)
    client = get_client(project)
    client.delete(f"/storage/{bucket}")
    typer.echo(f"Bucket '{bucket}' deleted.")


@app.command("set-access")
def set_access(
        bucket: str = typer.Argument(help="Bucket identifier (internal_domain)."),
        access_level: str = typer.Argument(help=f"Access level: {', '.join(ACCESS_LEVELS)}."),
        json: bool = typer.Option(False, "--json", help="Output as JSON."),
        project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Change a bucket's access policy."""
    if access_level not in ACCESS_LEVELS:
        typer.echo(f"Error: access_level must be one of: {', '.join(ACCESS_LEVELS)}", err=True)
        raise typer.Exit(code=1)
    client = get_client(project)
    resp = client.patch(f"/storage/{bucket}", data={"access_level": access_level})
    data = resp.get("data", {})
    if json:
        print_json(data)
        return
    typer.echo(f"Bucket '{data.get('internal_domain')}' access level set to '{data.get('access_level')}'.")


# ── files ─────────────────────────────────────────────────────────────────


@files_app.command("list")
def list_files(
        bucket: str = typer.Argument(help="Bucket identifier (internal_domain)."),
        path: str = typer.Option("", "--path", help="Folder path prefix."),
        page_size: int = typer.Option(100, "--page-size", help="Items per page (1-1000)."),
        continuation_token: str | None = typer.Option(
            None, "--continuation-token", help="Continuation token from previous page."
        ),
        fetch_all: bool = typer.Option(False, "--all", help="Fetch all pages and print together."),
        json: bool = typer.Option(False, "--json", help="Output as JSON."),
        project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """List files and folders in a bucket."""
    client = get_client(project)

    items: list[dict[str, Any]] = []
    next_token = continuation_token
    while True:
        params: dict[str, Any] = {"path": path, "page_size": page_size}
        if next_token:
            params["continuation_token"] = next_token
        resp = client.get(f"/storage/{bucket}/files", params=params)
        data = resp.get("data", {})
        items.extend(data.get("items", []) or [])
        next_token = data.get("next_continuation_token")
        if not fetch_all or not next_token:
            break

    if json:
        print_json({
            "items": items,
            "next_continuation_token": next_token,
        })
        return

    output([_format_file_row(it) for it in items], FILE_COLUMNS, title=f"Files in {bucket}")
    if next_token and not fetch_all:
        console.print(
            f"\n[dim]More results available. Use --continuation-token={next_token} "
            f"to fetch the next page, or --all to fetch everything.[/dim]"
        )


@files_app.command("upload")
def upload_file(
        bucket: str = typer.Argument(help="Bucket identifier (internal_domain)."),
        local_file: Path = typer.Argument(help="Local file to upload.", exists=True, dir_okay=False,
                                          readable=True),
        key: str | None = typer.Option(None, "--key", help="Remote key (defaults to the local file name)."),
        content_type: str | None = typer.Option(None, "--content-type", help="Content-Type header for the object."),
        project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Upload a local file to a bucket using a pre-signed URL."""
    client = get_client(project)

    remote_key = key or local_file.name
    metadata: dict[str, Any] = {}
    if content_type:
        metadata["ContentType"] = content_type

    resp = client.post(f"/storage/{bucket}/files/upload", data={"key": remote_key, "metadata": metadata})
    upload_url = (resp.get("data") or {}).get("upload_url")
    if not upload_url:
        typer.echo("Error: server did not return an upload URL.", err=True)
        raise typer.Exit(code=1)

    file_size = local_file.stat().st_size

    progress = Progress(
        TextColumn("[bold]Uploading[/bold] {task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        console=console,
    )
    with progress:
        task_id = progress.add_task(local_file.name, total=file_size)

        def _iter_file():
            with local_file.open("rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    progress.update(task_id, advance=len(chunk))
                    yield chunk

        headers = {"Content-Length": str(file_size)}
        if content_type:
            headers["Content-Type"] = content_type

        try:
            with httpx.Client(timeout=None) as http:
                put_resp = http.put(upload_url, content=_iter_file(), headers=headers)
        except httpx.HTTPError as e:
            typer.echo(f"Error: upload failed: {e}", err=True)
            raise typer.Exit(code=1) from e

    if put_resp.status_code >= 400:
        typer.echo(
            f"Error: upload failed with status {put_resp.status_code}: {put_resp.text[:200]}",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(f"Uploaded {local_file} -> {bucket}/{remote_key}")


@files_app.command("download")
def download_file(
        bucket: str = typer.Argument(help="Bucket identifier (internal_domain)."),
        key: str = typer.Argument(help="Remote key to download."),
        output_path: Path | None = typer.Option(
            None, "--output", "-o", help="Output path (defaults to the key's basename in cwd)."
        ),
        duration: int | None = typer.Option(
            None, "--duration", help="Pre-signed URL lifetime in seconds."
        ),
        project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Download an object from a bucket using a pre-signed URL."""
    client = get_client(project)
    body: dict[str, Any] = {"key": key}
    if duration is not None:
        body["duration"] = duration

    resp = client.post(f"/storage/{bucket}/files/download", data=body)
    download_url = (resp.get("data") or {}).get("upload_url")
    if not download_url:
        typer.echo("Error: server did not return a download URL.", err=True)
        raise typer.Exit(code=1)

    target = output_path or Path(os.path.basename(key) or "download.bin")
    if target.is_dir():
        target = target / (os.path.basename(key) or "download.bin")

    progress = Progress(
        TextColumn("[bold]Downloading[/bold] {task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        console=console,
    )
    try:
        with httpx.Client(timeout=None) as http:
            with http.stream("GET", download_url) as stream:
                if stream.status_code >= 400:
                    body_text = stream.read().decode(errors="replace")
                    typer.echo(
                        f"Error: download failed with status {stream.status_code}: {body_text[:200]}",
                        err=True,
                    )
                    raise typer.Exit(code=1)
                total = int(stream.headers.get("Content-Length") or 0) or None
                with progress:
                    task_id = progress.add_task(key, total=total)
                    with target.open("wb") as f:
                        for chunk in stream.iter_bytes(chunk_size=1024 * 1024):
                            f.write(chunk)
                            progress.update(task_id, advance=len(chunk))
    except httpx.HTTPError as e:
        typer.echo(f"Error: download failed: {e}", err=True)
        raise typer.Exit(code=1) from e

    typer.echo(f"Downloaded {bucket}/{key} -> {target}")


@files_app.command("rename")
def rename_file(
        bucket: str = typer.Argument(help="Bucket identifier (internal_domain)."),
        key: str = typer.Argument(help="Current key."),
        new_key: str = typer.Argument(help="New key."),
        project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Rename (move) a file within a bucket."""
    client = get_client(project)
    client.post(f"/storage/{bucket}/files/rename", data={"key": key, "new_key": new_key})
    typer.echo(f"Renamed {bucket}/{key} -> {bucket}/{new_key}")


@files_app.command("delete")
def delete_files(
        bucket: str = typer.Argument(help="Bucket identifier (internal_domain)."),
        keys: list[str] = typer.Argument(help="Keys to delete (one or more)."),
        force: bool = typer.Option(False, "--force", help="Skip confirmation."),
        project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Delete one or more files from a bucket."""
    if not keys:
        typer.echo("Error: at least one key is required.", err=True)
        raise typer.Exit(code=1)

    if not force:
        typer.echo(f"About to delete {len(keys)} object(s) from bucket '{bucket}':")
        for k in keys:
            typer.echo(f"  - {k}")
        if not typer.confirm("Continue?", default=False):
            typer.echo("Aborted.", err=True)
            raise typer.Exit(code=1)

    client = get_client(project)
    resp = client.post(f"/storage/{bucket}/files/delete", data={"keys": keys})
    deleted = (resp.get("data") or {}).get("deleted", len(keys))
    typer.echo(f"Deleted {deleted} object(s) from '{bucket}'.")


# ── keys ──────────────────────────────────────────────────────────────────


@keys_app.command("list")
def list_keys(
        json: bool = typer.Option(False, "--json", help="Output as JSON."),
        project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """List S3 access keys. 'Key' column is the identifier to pass to other commands."""
    client = get_client(project)
    resp = client.get("/storage/keys")
    data = resp.get("data", [])
    if json:
        print_json(data)
        return
    output([_format_key_row(k) for k in data], KEY_COLUMNS, title="S3 Access Keys")


def _print_key_table(data: dict[str, Any], *, show_secret: bool) -> None:
    console.print("")
    table = Table(title=f"Access Key: {data.get('name', '')}")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Name", str(data.get("name", "")))
    table.add_row("Identifier", str(data.get("internal_name", "")))
    table.add_row("Access Key", str(data.get("key_id", "")))
    if show_secret and data.get("key_secret"):
        table.add_row("Secret Key", str(data["key_secret"]))
        table.add_row("", "[dim]Save this value now — it will not be shown again.[/dim]")
    console.print(table)

    perms = data.get("permissions") or []
    if perms:
        perm_table = Table(title="Bucket Permissions")
        perm_table.add_column("Bucket")
        perm_table.add_column("Access")
        for p in perms:
            perm_table.add_row(str(p.get("bucket", "")), str(p.get("permissions", "")))
        console.print(perm_table)


@keys_app.command("create")
def create_key(
        name: str = typer.Argument(help="Display name (3-60 chars, alphanumeric, dot, dash). A unique"
                                        " identifier suffix is appended automatically."),
        bucket: list[str] = typer.Option(
            [],
            "--bucket",
            "-b",
            help="Bucket permission in format <bucket>:<ro|rw> (bucket = internal_domain)."
                 " Repeat for multiple buckets.",
        ),
        json: bool = typer.Option(False, "--json", help="Output as JSON."),
        project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Create a new S3 access key. The secret is shown once on success."""
    if not bucket:
        typer.echo("Error: at least one --bucket is required.", err=True)
        raise typer.Exit(code=1)

    permissions = [
        {"bucket": b_name, "permissions": p} for b_name, p in (_parse_bucket_perm(b) for b in bucket)
    ]

    client = get_client(project)
    resp = client.post("/storage/keys", data={"name": name, "permissions": permissions})
    data = resp.get("data", {})
    if json:
        print_json(data)
        return
    _print_key_table(data, show_secret=True)


@keys_app.command("update")
def update_key(
        key: str = typer.Argument(help="Key identifier (internal_name)."),
        new_name: str | None = typer.Option(None, "--name", help="New display name."),
        bucket: list[str] = typer.Option(
            [],
            "--bucket",
            "-b",
            help="Replace permissions; format <bucket>:<ro|rw>. Repeat for multiple buckets.",
        ),
        replace_permissions: bool = typer.Option(
            False,
            "--replace-permissions",
            help="Replace permissions with the provided --bucket list (pass with no --bucket to clear).",
        ),
        json: bool = typer.Option(False, "--json", help="Output as JSON."),
        project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Update a key's name and/or bucket permissions."""
    update_perms = replace_permissions or bool(bucket)
    if new_name is None and not update_perms:
        typer.echo(
            "Error: provide --name and/or --bucket (use --replace-permissions with no --bucket to clear).",
            err=True,
        )
        raise typer.Exit(code=1)

    payload: dict[str, Any] = {}
    if new_name is not None:
        payload["name"] = new_name
    if update_perms:
        payload["permissions"] = [
            {"bucket": b_name, "permissions": p} for b_name, p in (_parse_bucket_perm(b) for b in bucket)
        ]

    client = get_client(project)
    resp = client.patch(f"/storage/keys/{key}", data=payload)
    data = resp.get("data", {})
    if json:
        print_json(data)
        return
    _print_key_table(data, show_secret=False)


@keys_app.command("regenerate")
def regenerate_key(
        key: str = typer.Argument(help="Key identifier (internal_name)."),
        force: bool = typer.Option(False, "--force", help="Skip confirmation."),
        json: bool = typer.Option(False, "--json", help="Output as JSON."),
        project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Regenerate the secret for a key. The old secret stops working immediately."""
    if not force and not typer.confirm(
            f"Regenerate secret for key '{key}'? The old secret stops working immediately.",
            default=False,
    ):
        typer.echo("Aborted.", err=True)
        raise typer.Exit(code=1)

    client = get_client(project)
    resp = client.post(f"/storage/keys/{key}/regenerate")
    data = resp.get("data", {})
    if json:
        print_json(data)
        return
    _print_key_table(data, show_secret=True)


@keys_app.command("delete")
def delete_key(
        key: str = typer.Argument(help="Key identifier (internal_name)."),
        force: bool = typer.Option(False, "--force", help="Skip the typed confirmation."),
        project: str = typer.Option("default", "--project", "-p", help="Project alias."),
) -> None:
    """Delete an access key."""
    _confirm_delete(f"This will permanently delete access key '{key}'.", force=force)
    client = get_client(project)
    client.delete(f"/storage/keys/{key}")
    typer.echo(f"Key '{key}' deleted.")
