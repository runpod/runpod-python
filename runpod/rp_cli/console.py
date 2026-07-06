"""console rendering for the rp cli.

live, animated lifecycle output: spinner-driven phase tracking for
deploys (with byte-level upload progress), per-resource provisioning
animation for dev sessions, and color-coded resource badges. engine
modules stay renderer-free; they emit duck-typed events and this
module turns them into pixels.
"""

import time
from typing import Dict, Iterable, List, Optional, Tuple

from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
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


def endpoint_link(endpoint_id: str) -> str:
    """short clickable endpoint reference (osc8 hyperlink)."""
    return (
        f"[link={endpoint_url(endpoint_id)}]"
        f"[accent.light]{endpoint_id} ↗[/accent.light][/link]"
    )




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
    for name, kind, hardware, endpoint_id in rows:
        tail = (
            "[dim]per-call[/dim]"
            if not endpoint_id or endpoint_id == "per-call"
            else endpoint_link(endpoint_id)
        )
        console.print(
            f"  [white]{name:<{w_name}}[/white]"
            f"  {kind_badge(kind)}"
            f"  [dim]{hardware:<{w_hw}}[/dim]"
            f"  {tail}"
        )


def reload_banner(module: str = "") -> None:
    console.print()
    detail = f" [dim]{module} changed[/dim]" if module else ""
    console.print(f" [accent]↻[/accent] [white]reloading[/white]{detail}")


# -- live deploy phases ------------------------------------------------------

_PHASE_LABELS = {
    "vendor": "resolving dependencies",
    "package": "packaging artifact",
    "upload": "uploading build",
    "endpoints": "reconciling endpoints",
}


_BAR_WIDTH = 22


def _bar(fraction: float) -> str:
    """slim one-line progress bar."""
    fraction = min(max(fraction, 0.0), 1.0)
    filled = int(fraction * _BAR_WIDTH)
    return (
        f"[accent]{'━' * filled}[/accent]"
        f"[grey30]{'━' * (_BAR_WIDTH - filled)}[/grey30]"
    )


class DeployEvents:
    """deploy_app event sink: animated phase list with live progress.

    each phase renders a spinner while active and collapses to a ✓ with
    a short summary when the next begins. vendoring streams package
    names as pip resolves them; upload renders a slim in-line bar.
    """

    def __init__(self) -> None:
        self._progress = Progress(
            SpinnerColumn("dots", style=ACCENT, finished_text=f"[ok]✓[/ok]"),
            TextColumn("[white]{task.description}[/white]"),
            TextColumn("{task.fields[detail]}"),
            console=console,
            transient=False,
        )
        self._current: Optional[TaskID] = None
        self._current_name = ""
        self._started = False
        self._package_count = 0
        self._upload_total = 0

    def _ensure_started(self) -> None:
        if not self._started:
            self._progress.start()
            self._started = True

    def _finish_current(self) -> None:
        if self._current is None:
            return
        # collapse the live detail into a short summary
        if self._current_name == "vendor" and self._package_count:
            summary = f"{self._package_count} packages"
        elif self._current_name == "upload" and self._upload_total:
            summary = f"{self._upload_total / 1048576:.1f} MB"
        else:
            summary = ""
        self._progress.update(
            self._current,
            detail=f"[dim]{summary}[/dim]",
            total=1,
            completed=1,
        )
        self._current = None

    def phase(self, name: str, detail: str = "") -> None:
        self._ensure_started()
        self._finish_current()
        self._current_name = name
        self._current = self._progress.add_task(
            _PHASE_LABELS.get(name, name),
            detail=f"[dim]{detail}[/dim]" if detail else "",
            total=None,
        )

    def vendor_progress(self, count: int, package: str) -> None:
        if self._current is None:
            return
        self._package_count = count
        self._progress.update(
            self._current,
            detail=f"[accent.light]{package}[/accent.light] [dim]· {count}[/dim]",
        )

    def upload_progress(self, sent: int, total: int) -> None:
        if self._current is None:
            return
        self._upload_total = total
        mb = 1048576
        self._progress.update(
            self._current,
            detail=(
                f"{_bar(sent / total)} "
                f"[dim]{sent / mb:.1f} / {total / mb:.1f} MB[/dim]"
            ),
        )

    def endpoint_ready(self, name: str, endpoint_id: str) -> None:
        # collected, not printed: endpoint urls render in the final
        # summary after the progress display stops
        self.endpoints: Dict[str, str] = getattr(self, "endpoints", {})
        self.endpoints[name] = endpoint_id

    def close(self) -> None:
        self._finish_current()
        if self._started:
            self._progress.stop()
            self._started = False


# -- live dev provisioning ----------------------------------------------------


class DevEvents:
    """DevSession event sink.

    provisioning renders transient spinner rows that vanish once the
    session is up (the resource table is the durable record). request
    lifecycle renders as a block: an accent dispatch line, a dim
    indented worker feed, and a check/cross verdict.
    """

    def __init__(self) -> None:
        self._progress: Optional[Progress] = None
        self._tasks: Dict[str, TaskID] = {}

    def session_starting(self) -> None:
        self._progress = Progress(
            SpinnerColumn("dots", style=ACCENT, finished_text="[ok]✓[/ok]"),
            TextColumn("[white]{task.description}[/white]"),
            TextColumn("[dim]{task.fields[detail]}[/dim]"),
            console=console,
            transient=True,
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
        self._row(name, "adopting")

    def ready(self, name: str, endpoint_id: str) -> None:
        if self._progress is not None and name in self._tasks:
            self._progress.update(
                self._tasks[name], detail="ready", total=1, completed=1
            )

    def deleted(self, name: str) -> None:
        console.print(f" [dim]− {name}[/dim]")

    # -- refresh diff --

    def resource_added(self, name: str, kind: str, hardware: str) -> None:
        console.print(
            f" [ok]+[/ok] [white]{name}[/white] "
            f"[dim]{kind} · {hardware}[/dim]"
        )

    def resource_changed(self, name: str, fields: List[str]) -> None:
        what = ", ".join(fields) if fields else "config"
        console.print(
            f" [warn]~[/warn] [white]{name}[/white] [dim]{what}[/dim]"
        )

    def resource_removed(self, name: str) -> None:
        console.print(f" [err]−[/err] [white]{name}[/white]")

    # -- request lifecycle (LiveTarget events) --
    # next.js/wrangler-style event lines: leading status glyph, the
    # function call in normal weight, everything else dim, timing at
    # the end. function stdout renders as `name │ line` so concurrent
    # calls interleave without losing attribution.

    def dispatch(self, name: str, label: str = "") -> None:
        detail = f" [dim]{label}[/dim]" if label else ""
        console.print(f" [accent]○[/accent] [white]{name}()[/white]{detail}")

    def worker_status(self, name: str, counts: Dict[str, int]) -> None:
        from runpod.apps.monitor import format_worker_counts

        console.print(
            f" [dim]○ {name}() waiting · {format_worker_counts(counts)}[/dim]"
        )

    def worker_ready(self, name: str, worker_id: str) -> None:
        console.print(
            f" [accent]●[/accent] [white]{name}()[/white] [dim]running on "
            f"worker {worker_id[:12]}[/dim]"
        )

    def worker_log(self, name: str, line: str) -> None:
        from rich.markup import escape

        console.print(f"   {_pipe(name)} {escape(line)}")

    def request_completed(self, name: str, elapsed_s: float) -> None:
        console.print(
            f" [ok]✓[/ok] [white]{name}()[/white] "
            f"[dim]in {_fmt_elapsed(elapsed_s)}[/dim]"
        )

    def request_failed(self, name: str, elapsed_s: float) -> None:
        console.print(
            f" [err]✗[/err] [white]{name}()[/white] "
            f"[dim]failed in {_fmt_elapsed(elapsed_s)}[/dim]"
        )


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


def _fmt_elapsed(elapsed: float) -> str:
    if elapsed < 1:
        return f"{elapsed * 1000:.0f}ms"
    if elapsed < 120:
        return f"{elapsed:.1f}s"
    return f"{int(elapsed // 60)}m{int(elapsed % 60):02d}s"


def entrypoint_header(fn_name: str = "") -> None:
    console.print()
    if fn_name:
        console.print(
            f" [accent]▸[/accent] [white]{fn_name}()[/white] "
            f"[dim]local entrypoint[/dim]"
        )


def entrypoint_success(elapsed: float) -> None:
    console.print()
    console.print(
        f" [ok]✓[/ok] [white]done[/white] [dim]in {_fmt_elapsed(elapsed)} "
        f"· enter re-run · edit reload · ^C quit[/dim]"
    )


def entrypoint_failure(elapsed: float, err: str) -> None:
    console.print()
    console.print(f" [err]✗ {err}[/err]")
    console.print(
        f" [err]✗[/err] [white]failed[/white] [dim]in {_fmt_elapsed(elapsed)} "
        f"· enter re-run · edit reload · ^C quit[/dim]"
    )
