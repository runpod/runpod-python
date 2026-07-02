"""console output for the rp cli.

timestamped, column-aligned lifecycle lines for dev sessions and
deploys, in the spirit of a compact build log:

    14:02:11 double   │ provisioning queue cpu3c-1-2
    14:02:19 double   │ ready qjjwnknp
    14:02:20 POST /double
    14:02:21 ✓ double 0.4s
"""

import time
from datetime import datetime
from typing import Iterable, Optional

from rich.console import Console

console = Console(highlight=False)

# shared name column width so pipes line up across resources
_name_width: int = 0


def set_name_width(names: Iterable[str]) -> None:
    global _name_width
    _name_width = max((len(n) for n in names), default=0)


def _padded(name: str) -> str:
    return f"{name:<{_name_width}}" if _name_width else name


def _ts() -> str:
    return f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim]"


def _pipe(name: str) -> str:
    return f"{_padded(name)} [dim]│[/dim]"


# -- generic --


def info(message: str) -> None:
    console.print(f"{_ts()} {message}")


def success(message: str) -> None:
    console.print(f"{_ts()} [green]✓[/green] {message}")


def error(message: str) -> None:
    console.print(f"{_ts()} [red]✗[/red] {message}")


def warn(message: str) -> None:
    console.print(f"{_ts()} [yellow]![/yellow] {message}")


def rule(title: str = "") -> None:
    console.rule(f"[dim]{title}[/dim]" if title else None, style="dim")


# -- resource lifecycle --


def resource_provisioning(name: str, kind: str, hardware: str) -> None:
    console.print(
        f"{_ts()} {_pipe(name)} [blue]provisioning[/blue] "
        f"[dim]{kind} {hardware}[/dim]"
    )


def resource_adopted(name: str, endpoint_id: str) -> None:
    console.print(
        f"{_ts()} {_pipe(name)} [blue]adopted[/blue] [dim]{endpoint_id}[/dim]"
    )


def resource_ready(name: str, endpoint_id: str) -> None:
    console.print(
        f"{_ts()} {_pipe(name)} [green]ready[/green] [dim]{endpoint_id}[/dim]"
    )


def resource_refreshed(name: str, generation: int) -> None:
    console.print(
        f"{_ts()} {_pipe(name)} [blue]refreshed[/blue] "
        f"[dim]generation {generation}[/dim]"
    )


def resource_deleted(name: str) -> None:
    console.print(f"{_ts()} {_pipe(name)} [dim]deleted[/dim]")


def resource_log(name: str, line: str) -> None:
    console.print(f"{_ts()} {_pipe(name)} {line}")


# -- request lifecycle --


def request_started(name: str, label: str = "") -> None:
    console.print(f"{_ts()} [white]CALL[/white] /{name} [dim]{label}[/dim]")


def request_completed(name: str, elapsed_s: float) -> None:
    console.print(
        f"{_ts()} [green]✓[/green] {_padded(name)} [dim]{elapsed_s:.1f}s[/dim]"
    )


def request_failed(name: str, elapsed_s: float, err: Optional[str] = None) -> None:
    console.print(
        f"{_ts()} [red]✗[/red] {_padded(name)} [dim]{elapsed_s:.1f}s[/dim]"
    )
    if err:
        console.print(f"         [dim]{err}[/dim]")


# -- dev session chrome --


def dev_banner(app_names: Iterable[str], module: str) -> None:
    apps = ", ".join(app_names)
    console.print()
    console.print(f"[green]✓[/green] [bold]rp dev[/bold]  [dim]{module}[/dim]")
    console.print(f"  [dim]apps[/dim]  {apps}")
    console.print()


def dev_resources_table(rows: Iterable[tuple]) -> None:
    """rows of (name, kind, hardware, endpoint_id)."""
    rows = list(rows)
    if not rows:
        console.print("  [dim](no queue/api resources; tasks run per-call)[/dim]")
        return
    w_name = max(len(r[0]) for r in rows)
    w_kind = max(len(r[1]) for r in rows)
    w_hw = max(len(r[2]) for r in rows)
    for name, kind, hardware, endpoint_id in rows:
        console.print(
            f"  [white]{name:<{w_name}}[/white]"
            f"  [dim]{kind:<{w_kind}}[/dim]"
            f"  [dim]{hardware:<{w_hw}}[/dim]"
            f"  [dim]{endpoint_id}[/dim]"
        )
    console.print()


def dev_hints() -> None:
    console.print(
        "  [dim]enter[/dim] re-run   [dim]edit[/dim] reload   "
        "[dim]ctrl-c[/dim] exit"
    )
    console.print()


def phase(name: str, detail: str = "") -> None:
    suffix = f" [dim]{detail}[/dim]" if detail else ""
    console.print(f"{_ts()} [blue]{name}[/blue]{suffix}")


class DeployEvents:
    """deploy_app event sink rendering phase lines."""

    def phase(self, name: str, detail: str = "") -> None:
        phase(name, detail)


class DevEvents:
    """DevSession event sink rendering lifecycle lines."""

    def provisioning(self, name: str, kind: str, hardware: str) -> None:
        resource_provisioning(name, kind, hardware)

    def adopted(self, name: str, endpoint_id: str) -> None:
        resource_adopted(name, endpoint_id)

    def ready(self, name: str, endpoint_id: str) -> None:
        resource_ready(name, endpoint_id)

    def refreshed(self, name: str, generation: int) -> None:
        resource_refreshed(name, generation)

    def deleted(self, name: str) -> None:
        resource_deleted(name)


class Timer:
    """context timer for elapsed reporting."""

    def __enter__(self) -> "Timer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc) -> None:
        self.elapsed = time.monotonic() - self._start

    @property
    def so_far(self) -> float:
        return time.monotonic() - self._start
