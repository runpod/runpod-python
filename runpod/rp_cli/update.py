"""self-update for the rp cli and a passive new-version notice.

`rp update` installs the latest (or a pinned) runpod release from pypi
using uv when available, pip otherwise. every other command starts a
daemon thread that checks pypi at most once per day (cached under
~/.runpod) and prints a one-line notice at exit when a newer version
exists. the check never blocks or crashes a command.
"""

from __future__ import annotations

import atexit
import json
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Set, Tuple

PYPI_URL = "https://pypi.org/pypi/runpod/json"
INSTALL_TIMEOUT_SECONDS = 120
CACHE_PATH = Path.home() / ".runpod" / "update_check.json"
CHECK_INTERVAL_HOURS = 24

_newer_version: Optional[str] = None
_check_done = threading.Event()
_started = threading.Event()
_start_lock = threading.Lock()


def current_version() -> str:
    try:
        from runpod import __version__

        return __version__
    except Exception:  # noqa: BLE001 - version must never break the cli
        return "unknown"


def parse_version(version: str) -> Tuple[int, ...]:
    """leading numeric release segments of a version string.

    dev/local suffixes (e.g. 1.8.0.dev3) are ignored so a dev build of
    the next release does not count as older than the current one.
    """
    parts = []
    for part in version.split("."):
        if not part.isdigit():
            break
        parts.append(int(part))
    return tuple(parts)


def compare_versions(a: Tuple[int, ...], b: Tuple[int, ...]) -> int:
    """negative when a < b, zero when equal, positive when a > b."""
    width = max(len(a), len(b))
    a_padded = a + (0,) * (width - len(a))
    b_padded = b + (0,) * (width - len(b))
    return (a_padded > b_padded) - (a_padded < b_padded)


def fetch_pypi_metadata() -> Tuple[str, Set[str]]:
    """(latest version, all release versions) from pypi."""
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=15) as resp:  # noqa: S310
            data = json.loads(resp.read().decode())
    except urllib.error.URLError as exc:
        if isinstance(exc, urllib.error.HTTPError):
            raise RuntimeError(
                f"pypi returned HTTP {exc.code}; try again later"
            ) from exc
        raise ConnectionError(
            "could not reach pypi; check your network connection"
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("pypi returned an unexpected response") from exc

    try:
        latest = data["info"]["version"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("pypi response missing version info") from exc
    return latest, set(data.get("releases", {}).keys())


def install_command(version: str) -> list:
    """the installer invocation targeting the running interpreter.

    `uv pip install` is used only with an explicit --python pointing at
    this interpreter: bare uv resolves against an activated venv, which
    is not necessarily where this cli runs from.
    """
    package_spec = f"runpod=={version}"
    if shutil.which("uv"):
        return [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            package_spec,
            "--quiet",
        ]
    return [sys.executable, "-m", "pip", "install", package_spec, "--quiet"]


def run_install(version: str) -> None:
    cmd = install_command(version)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=INSTALL_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        installer = "uv" if cmd[0] == "uv" else "pip"
        raise RuntimeError(
            f"{installer} install failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )


# -- passive background check -------------------------------------------


def _read_cache() -> Optional[dict]:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _write_cache(latest_version: str) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(
            json.dumps(
                {
                    "last_checked_utc": datetime.now(
                        timezone.utc
                    ).isoformat(),
                    "latest_version": latest_version,
                }
            ),
            encoding="utf-8",
        )
    except OSError:
        # cache write is best-effort; the check reruns next invocation
        pass


def _cache_fresh(cache: dict) -> bool:
    try:
        last_checked = datetime.fromisoformat(cache["last_checked_utc"])
        elapsed = (
            datetime.now(timezone.utc) - last_checked
        ).total_seconds() / 3600
        return elapsed < CHECK_INTERVAL_HOURS
    except (KeyError, ValueError, TypeError):
        return False


def _run_check() -> None:
    global _newer_version  # noqa: PLW0603
    try:
        current = current_version()
        if current == "unknown":
            return

        cache = _read_cache()
        latest = None
        if cache and _cache_fresh(cache):
            latest = cache.get("latest_version") or None
        if not latest:
            latest, _ = fetch_pypi_metadata()
            _write_cache(latest)
        if not latest:
            return

        if compare_versions(parse_version(latest), parse_version(current)) > 0:
            _newer_version = latest
    except Exception:  # noqa: BLE001 - the check must never crash the cli
        pass
    finally:
        _check_done.set()


def _print_update_notice() -> None:
    # plain text: atexit runs after rich teardown
    if not _check_done.is_set():
        return
    if _newer_version:
        print(
            f"\na new version of runpod is available: {_newer_version}\n"
            "  run 'rp update' to upgrade.",
            file=sys.stderr,
        )


def _is_interactive() -> bool:
    for stream in (sys.stderr, sys.stdout):
        try:
            if stream is not None and stream.isatty():
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def start_background_check() -> None:
    """spawn the daemon check thread once per process (ttys only)."""
    with _start_lock:
        if _started.is_set():
            return
        _started.set()
    if not _is_interactive():
        return
    atexit.register(_print_update_notice)
    threading.Thread(target=_run_check, daemon=True).start()
