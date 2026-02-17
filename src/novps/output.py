from __future__ import annotations

import json
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

console = Console()


def print_table(data: list[dict[str, Any]], columns: list[tuple[str, str]], title: str | None = None) -> None:
    """Print data as a Rich table.

    columns: list of (key, header) tuples.
    """
    table = Table(title=title)
    for _, header in columns:
        table.add_column(header)
    for row in data:
        table.add_row(*(str(row.get(key, "")) for key, _ in columns))
    console.print(table)


def print_json(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2))


def output(
    data: list[dict[str, Any]],
    columns: list[tuple[str, str]],
    title: str | None = None,
    *,
    as_json: bool = False,
) -> None:
    if as_json:
        print_json(data)
    else:
        print_table(data, columns, title)
