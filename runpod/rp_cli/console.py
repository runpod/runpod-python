"""console rendering for the rp cli.

live, animated lifecycle output: spinner-driven phase tracking for
deploys (with byte-level upload progress), per-resource provisioning
animation for dev sessions, and color-coded resource badges. engine
modules stay renderer-free; they emit duck-typed events and this
module turns them into pixels.
"""

import time
from datetime import datetime
from typing import Dict, Iterable, Optional, Tuple

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import (
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

# -- brand ----------------------------------------------------------------

ACCENT = "#673de6"  # runpod purple
ACCENT_LIGHT = "#a78bfa"
OK = "#34d399"
ERR = "#f87171"
WARN = "#fbbf24"
DIM = "grey58"

KIND_STYLES: Dict[str, str] = {
    "queue": "bold cyan",
    "api": "bold magenta",
    "task": "bold yellow",
}

theme = Theme(
    {
        "accent": ACCENT,
        "accent.light": ACCENT_LIGHT,
        "ok": OK,
        "err": ERR,
        "warn": WARN,
        "dim": DIM,
    }
)

console = Console(highlight=False, theme=theme)

_name_width: int = 0


def set_name_width(names: Iterable[str]) -> None:
    global _name_width
    _name_width = max((len(n) for n in names), default=0)


def _padded(name: str) -> str:
    return f"{name:<{_name_width}}" if _name_width else name


def _ts() -> str:
    return f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim]"


def _pipe(name: str) -> str:
    return f"[accent.light]{_padded(name)}[/accent.light] [dim]│[/dim]"


def kind_badge(kind: str) -> str:
    style = KIND_STYLES.get(kind, "bold white")
    return f"[{style}]{kind:<5}[/{style}]"


# -- generic lines ---------------------------------------------------------


def info(message: str) -> None:
    console.print(f"{_ts()} {message}")


def success(message: str) -> None:
    console.print(f"{_ts()} [ok]✓[/ok] {message}")


def error(message: str) -> None:
    console.print(f"{_ts()} [err]✗[/err] {message}")


def warn(message: str) -> None:
    console.print(f"{_ts()} [warn]![/warn] {message}")


def rule(title: str = "") -> None:
    console.rule(f"[accent]{title}[/accent]" if title else None, style="dim")


# -- request lifecycle -----------------------------------------------------


def request_started(name: str, label: str = "") -> None:
    console.print(
        f"{_ts()} [bold white]CALL[/bold white] [accent.light]/{name}[/accent.light]"
        f" [dim]{label}[/dim]"
    )


def request_completed(name: str, elapsed_s: float) -> None:
    console.print(
        f"{_ts()} [ok]✓[/ok] {_padded(name)} [dim]{elapsed_s:.1f}s[/dim]"
    )


def request_failed(name: str, elapsed_s: float, err: Optional[str] = None) -> None:
    console.print(
        f"{_ts()} [err]✗[/err] {_padded(name)} [dim]{elapsed_s:.1f}s[/dim]"
    )
    if err:
        console.print(f"         [err dim]{err}[/err dim]")


# -- banners ---------------------------------------------------------------


def _brand_title(subtitle: str) -> Text:
    title = Text()
    title.append("◤ ", style=ACCENT)
    title.append("runpod", style=f"bold {ACCENT_LIGHT}")
    title.append(" ▸ ", style=DIM)
    title.append(subtitle, style="bold white")
    return title


def dev_banner(app_names: Iterable[str], module: str) -> None:
    body = Text()
    body.append("apps    ", style=DIM)
    body.append(", ".join(app_names), style="white")
    body.append("\nmodule  ", style=DIM)
    body.append(module, style="white")
    console.print()
    console.print(
        Panel(
            body,
            title=_brand_title("dev"),
            title_align="left",
            border_style=ACCENT,
            box=box.ROUNDED,
            padding=(0, 2),
            expand=False,
        )
    )


def deploy_banner(app_name: str, env: str, resources: Iterable[Tuple[str, str]]) -> None:
    """resources: (name, kind) pairs."""
    body = Text()
    first = True
    for name, kind in resources:
        if not first:
            body.append("\n")
        body.append(f"{kind:<6}", style=KIND_STYLES.get(kind, "white"))
        body.append(f" {name}", style="white")
        first = False
    if first:
        body.append("(no resources)", style=DIM)
    console.print()
    console.print(
        Panel(
            body,
            title=_brand_title(f"deploy · {app_name} → {env}"),
            title_align="left",
            border_style=ACCENT,
            box=box.ROUNDED,
            padding=(0, 2),
            expand=False,
        )
    )


def resources_table(rows: Iterable[tuple]) -> None:
    """rows of (name, kind, hardware, endpoint_id)."""
    rows = list(rows)
    if not rows:
        console.print("  [dim](no resources)[/dim]")
        return
    table = Table(
        box=box.SIMPLE_HEAD,
        border_style="dim",
        header_style=f"bold {ACCENT_LIGHT}",
        pad_edge=False,
        padding=(0, 2),
    )
    table.add_column("resource", style="bold white")
    table.add_column("kind")
    table.add_column("hardware", style="dim")
    table.add_column("endpoint", style=ACCENT_LIGHT)
    for name, kind, hardware, endpoint_id in rows:
        table.add_row(
            name,
            kind_badge(kind),
            hardware,
            endpoint_id or "[dim]—[/dim]",
        )
    console.print(table)


def dev_hints() -> None:
    console.print(
        f"  [{ACCENT_LIGHT}]⏎[/{ACCENT_LIGHT}] [dim]re-run[/dim]   "
        f"[{ACCENT_LIGHT}]✎[/{ACCENT_LIGHT}] [dim]edit to reload[/dim]   "
        f"[{ACCENT_LIGHT}]^C[/{ACCENT_LIGHT}] [dim]exit[/dim]"
    )
    console.print()


def reload_flash() -> None:
    console.print()
    console.rule(f"[{WARN}]⚡ reload[/{WARN}]", style=WARN)


# -- live deploy phases ------------------------------------------------------

_PHASE_LABELS = {
    "vendor": "resolving dependencies",
    "package": "packaging artifact",
    "upload": "uploading build",
    "endpoints": "reconciling endpoints",
}


class DeployEvents:
    """deploy_app event sink: animated phase list with live progress.

    each phase renders a spinner while active and collapses to a ✓ with
    elapsed time when the next phase begins. upload progress (bytes)
    animates in-line when the transport reports it.
    """

    def __init__(self) -> None:
        self._progress = Progress(
            SpinnerColumn("dots", style=ACCENT, finished_text=f"[ok]✓[/ok]"),
            TextColumn("[white]{task.description}[/white]"),
            TextColumn("[dim]{task.fields[detail]}[/dim]"),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        )
        self._current: Optional[TaskID] = None
        self._started = False

    def _ensure_started(self) -> None:
        if not self._started:
            self._progress.start()
            self._started = True

    def phase(self, name: str, detail: str = "") -> None:
        self._ensure_started()
        if self._current is not None:
            self._progress.update(self._current, total=1, completed=1)
        self._current = self._progress.add_task(
            _PHASE_LABELS.get(name, name), detail=detail, total=None
        )

    def upload_progress(self, sent: int, total: int) -> None:
        if self._current is None:
            return
        mb = 1024 * 1024
        self._progress.update(
            self._current,
            detail=f"{sent / mb:.1f} / {total / mb:.1f} MB",
        )

    def endpoint_ready(self, name: str, endpoint_id: str) -> None:
        self._progress.console.print(
            f"  {_pipe(name)} [ok]ready[/ok] [dim]{endpoint_id}[/dim]"
        )

    def close(self) -> None:
        if self._current is not None:
            self._progress.update(self._current, total=1, completed=1)
            self._current = None
        if self._started:
            self._progress.stop()
            self._started = False


# -- live dev provisioning ----------------------------------------------------


class DevEvents:
    """DevSession event sink: per-resource spinner rows during start,
    plain lifecycle lines afterwards.
    """

    def __init__(self) -> None:
        self._progress: Optional[Progress] = None
        self._tasks: Dict[str, TaskID] = {}

    def session_starting(self) -> None:
        self._progress = Progress(
            SpinnerColumn("dots", style=ACCENT, finished_text="[ok]✓[/ok]"),
            TextColumn("[accent.light]{task.description}[/accent.light]"),
            TextColumn("[dim]{task.fields[detail]}[/dim]"),
            console=console,
        )
        self._progress.start()

    def session_started(self) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
            self._tasks.clear()

    def _row(self, name: str, detail: str) -> None:
        if self._progress is None:
            info(f"{_pipe(name)} {detail}")
            return
        if name not in self._tasks:
            self._tasks[name] = self._progress.add_task(
                _padded(name), detail=detail, total=None
            )
        else:
            self._progress.update(self._tasks[name], detail=detail)

    def provisioning(self, name: str, kind: str, hardware: str) -> None:
        self._row(name, f"provisioning {kind} · {hardware}")

    def adopted(self, name: str, endpoint_id: str) -> None:
        self._row(name, f"adopted {endpoint_id}")

    def ready(self, name: str, endpoint_id: str) -> None:
        if self._progress is not None and name in self._tasks:
            self._progress.update(
                self._tasks[name],
                detail=f"ready · {endpoint_id}",
                total=1,
                completed=1,
            )
        else:
            console.print(
                f"{_ts()} {_pipe(name)} [ok]ready[/ok] [dim]{endpoint_id}[/dim]"
            )

    def refreshed(self, name: str, generation: int) -> None:
        console.print(
            f"{_ts()} {_pipe(name)} [accent]refreshed[/accent] "
            f"[dim]generation {generation}[/dim]"
        )

    def deleted(self, name: str) -> None:
        console.print(f"{_ts()} {_pipe(name)} [dim]deleted[/dim]")


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


def entrypoint_header() -> None:
    console.print()
    console.rule(f"[{ACCENT_LIGHT}]▶ entrypoint[/{ACCENT_LIGHT}]", style="dim")


def entrypoint_success(elapsed: float) -> None:
    console.rule(f"[ok]✓ {elapsed:.1f}s[/ok]", style="dim")


def entrypoint_failure(elapsed: float, err: str) -> None:
    console.rule(f"[err]✗ {elapsed:.1f}s[/err]", style="dim")
    console.print(f"  [err]{err}[/err]")


def session_summary(runs: int, reloads: int, elapsed: float) -> None:
    minutes, seconds = divmod(int(elapsed), 60)
    console.print()
    console.print(
        Panel(
            Text.assemble(
                ("runs ", DIM),
                (str(runs), "white"),
                ("   reloads ", DIM),
                (str(reloads), "white"),
                ("   session ", DIM),
                (f"{minutes}m {seconds}s" if minutes else f"{seconds}s", "white"),
            ),
            border_style="dim",
            box=box.ROUNDED,
            padding=(0, 2),
            expand=False,
        )
    )
