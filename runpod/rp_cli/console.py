"""console rendering for the rp cli.

live, animated lifecycle output: spinner-driven phase tracking for
deploys (with byte-level upload progress), per-resource provisioning
animation for dev sessions, and color-coded resource badges. engine
modules stay renderer-free; they emit duck-typed events and this
module turns them into pixels.
"""

import time
from typing import Dict, Iterable, Optional, Tuple

from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
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
        "progress.elapsed": DIM,
    }
)

console = Console(highlight=False, theme=theme)

CONSOLE_URL = "https://console.runpod.io/serverless/user/endpoint"


def endpoint_url(endpoint_id: str) -> str:
    return f"{CONSOLE_URL}/{endpoint_id}?tab=overview"




_name_width: int = 0


def set_name_width(names: Iterable[str]) -> None:
    global _name_width
    _name_width = max((len(n) for n in names), default=0)


def _padded(name: str) -> str:
    return f"{name:<{_name_width}}" if _name_width else name


def _pipe(name: str) -> str:
    return f"[accent.light]{_padded(name)}[/accent.light] [dim]│[/dim]"


def kind_badge(kind: str) -> str:
    style = KIND_STYLES.get(kind, "bold white")
    return f"[{style}]{kind:<5}[/{style}]"


# -- generic lines ---------------------------------------------------------


def info(message: str) -> None:
    console.print(f"{message}")


def success(message: str) -> None:
    console.print(f"[ok]✓[/ok] {message}")


def error(message: str) -> None:
    console.print(f"[err]✗[/err] {message}")


def warn(message: str) -> None:
    console.print(f"[warn]![/warn] {message}")


def rule(title: str = "") -> None:
    console.rule(f"[accent]{title}[/accent]" if title else None, style="dim")


# -- request lifecycle -----------------------------------------------------


def request_started(name: str, label: str = "") -> None:
    console.print(
        f"[bold white]CALL[/bold white] [accent.light]/{name}[/accent.light]"
        f" [dim]{label}[/dim]"
    )


def request_completed(name: str, elapsed_s: float) -> None:
    console.print(
        f"[ok]✓[/ok] {_padded(name)} [dim]{elapsed_s:.1f}s[/dim]"
    )


def request_failed(name: str, elapsed_s: float, err: Optional[str] = None) -> None:
    console.print(
        f"[err]✗[/err] {_padded(name)} [dim]{elapsed_s:.1f}s[/dim]"
    )
    if err:
        console.print(f"         [err dim]{err}[/err dim]")


# -- banners ---------------------------------------------------------------


def dev_banner(app_names: Iterable[str], module: str) -> None:
    console.print(
        f"[bold white]dev[/bold white] [dim]{', '.join(app_names)} · {module}[/dim]"
    )


def deploy_banner(app_name: str, env: str, resources: Iterable[Tuple[str, str]]) -> None:
    """resources: (name, kind) pairs."""
    names = ", ".join(name for name, _ in resources) or "(no resources)"
    console.print(
        f"[bold white]deploy[/bold white] [dim]{app_name} → {env} · {names}[/dim]"
    )


def resources_table(rows: Iterable[tuple]) -> None:
    """rows of (name, kind, hardware, endpoint_id)."""
    rows = list(rows)
    if not rows:
        return
    w_name = max(len(r[0]) for r in rows)
    w_hw = max(len(r[2]) for r in rows)
    console.print()
    for name, kind, hardware, endpoint_id in rows:
        tail = (
            "[dim]per-call[/dim]"
            if not endpoint_id or endpoint_id == "per-call"
            else ""
        )
        console.print(
            f"  [white]{name:<{w_name}}[/white]"
            f"  {kind_badge(kind)}"
            f"  [dim]{hardware:<{w_hw}}[/dim]"
            f"  {tail}"
        )
        if endpoint_id and endpoint_id != "per-call":
            console.print(
                f"  [accent.light]{endpoint_url(endpoint_id)}[/accent.light]"
            )
    console.print()


def reload_flash() -> None:
    console.print(f"[warn]⚡ reload[/warn]")


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
        # collected, not printed: endpoint urls render in the final
        # summary after the progress display stops
        self.endpoints: Dict[str, str] = getattr(self, "endpoints", {})
        self.endpoints[name] = endpoint_id

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
                detail="ready",
                total=1,
                completed=1,
            )
        else:
            console.print(f"{_pipe(name)} [ok]ready[/ok]")

    def refreshed(self, name: str, generation: int) -> None:
        console.print(
            f"{_pipe(name)} [accent]refreshed[/accent] "
            f"[dim]generation {generation}[/dim]"
        )

    def deleted(self, name: str) -> None:
        console.print(f"{_pipe(name)} [dim]deleted[/dim]")


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


def entrypoint_success(elapsed: float) -> None:
    console.print(
        f"[ok]✓[/ok] [dim]{elapsed:.1f}s · enter to re-run · edit to reload · ^C to quit[/dim]"
    )


def entrypoint_failure(elapsed: float, err: str) -> None:
    console.print(f"[err]✗ {err}[/err] [dim]{elapsed:.1f}s[/dim]")
