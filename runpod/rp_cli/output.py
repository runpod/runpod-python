"""output, picker, prompt, and completion helpers shared by resource commands."""

import json
import sys
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import click
from click.shell_completion import CompletionItem

Column = tuple[str, Callable[[dict], Any]]

json_option = click.option(
    "--json", "as_json", is_flag=True, help="Print JSON for scripts and agents."
)


def print_json(data: Any) -> None:
    click.echo(json.dumps(data, indent=2, default=str))


def cell(value: Any) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def table(rows: Sequence[dict], columns: Sequence[Column]):
    from rich.markup import escape
    from rich.table import Table

    result = Table(box=None, header_style="dim", pad_edge=False)
    for index, (header, _) in enumerate(columns):
        result.add_column(header, no_wrap=index == 0, overflow="fold")
    for row in rows:
        result.add_row(*(escape(cell(value(row))) for _, value in columns))
    return result


def print_table(rows: Sequence[dict], columns: Sequence[Column], empty: str) -> None:
    from runpod.rp_cli import console as ui

    ui.console.print(table(rows, columns) if rows else f"[dim]{empty}[/dim]")


def print_fields(record: dict, fields: Sequence[Column]) -> None:
    from rich.markup import escape
    from rich.table import Table

    from runpod.rp_cli import console as ui

    result = Table.grid(padding=(0, 2))
    result.add_column(style="dim", no_wrap=True)
    result.add_column(overflow="fold")
    for label, value in fields:
        result.add_row(label, escape(cell(value(record))))
    ui.console.print(result)


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def pick(message: str, choices: Sequence[tuple[str, str]], missing: str) -> str:
    if not interactive():
        raise click.UsageError(missing)
    if not choices:
        raise click.UsageError(f"{missing} (none found)")
    from InquirerPy import inquirer
    from InquirerPy.base.control import Choice

    return inquirer.fuzzy(
        message=message,
        choices=[Choice(value, name=label) for value, label in choices],
    ).execute()


def confirm(message: str, yes: bool) -> None:
    if yes:
        return
    if not interactive():
        raise click.UsageError("pass --yes to confirm in a non-interactive shell")
    click.confirm(message, abort=True)


def completer(
    fetch: Callable[[], Iterable[tuple[str, str]]],
) -> Callable[[click.Context, click.Parameter, str], list[CompletionItem]]:
    """shell completion offering (value, help) pairs from fetch."""

    def complete(ctx, param, incomplete):
        try:
            entries = list(fetch())
        except Exception:
            return []
        return [
            CompletionItem(value, help=label)
            for value, label in entries
            if value.startswith(incomplete)
        ]

    return complete
