"""output, picker, prompt, and completion helpers shared by resource commands."""

import contextlib
import json
import sys
from typing import Any, Callable, Iterable, Iterator, Sequence

import click
from click.shell_completion import CompletionItem

from runpod import error
from runpod.rp_cli.compute import ResolutionError

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


def print_table(rows: Sequence[dict], columns: Sequence[Column], empty: str) -> None:
    """render rows as an aligned table, or a dim notice when empty."""
    from rich.markup import escape
    from rich.table import Table

    from runpod.rp_cli import console as ui

    if not rows:
        ui.console.print(f"[dim]{empty}[/dim]")
        return
    table = Table(box=None, header_style="dim", pad_edge=False)
    for header, _ in columns:
        table.add_column(header)
    for row in rows:
        table.add_row(*(escape(cell(value(row))) for _, value in columns))
    ui.console.print(table)


def print_fields(record: dict, fields: Sequence[Column]) -> None:
    """render one record as aligned label/value lines."""
    from rich.markup import escape

    from runpod.rp_cli import console as ui

    width = max(len(label) for label, _ in fields)
    for label, value in fields:
        ui.console.print(f"[dim]{label:<{width}}[/dim]  {escape(cell(value(record)))}")


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def pick(message: str, choices: Sequence[tuple[str, str]], missing: str) -> str:
    """let a person choose a value from (value, label) pairs.

    scripts and agents get a usage error naming what to pass instead.
    """
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
    """ask before a destructive action unless --yes was passed."""
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
        except Exception:  # noqa: BLE001 - completion must never break the shell
            return []
        return [
            CompletionItem(value, help=label)
            for value, label in entries
            if value.startswith(incomplete)
        ]

    return complete


@contextlib.contextmanager
def api_errors() -> Iterator[None]:
    """surface api and resolution failures as clean cli errors."""
    try:
        yield
    except (error.RunPodError, ResolutionError) as exc:
        raise click.ClickException(str(exc)) from exc
