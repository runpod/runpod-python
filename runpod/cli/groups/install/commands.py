"""
CLI commands for installing optional runpod binaries.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

import runpod
from runpod.version import get_version

from .functions import (
    BinaryChecksumMismatch,
    BinaryDownloadError,
    download_gpu_test_binary,
)


def _default_install_path() -> Path:
    """Package-local binaries dir — the same path _binary_helpers checks."""
    return Path(runpod.__file__).parent / "serverless" / "binaries" / "gpu_test"


@click.command(
    "install-gpu-test",
    help=(
        "Download the optional gpu_test CUDA health-check binary from the "
        "GitHub release matching the installed runpod version. "
        "Runpod GPU workers only — no-op on CPU-only environments."
    ),
)
@click.option(
    "--version",
    "version",
    default=None,
    help="Release tag to download (defaults to installed runpod version).",
)
@click.option(
    "--dest",
    "dest",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    default=None,
    help="Override destination path. Defaults to the package's binaries dir.",
)
def install_gpu_test_cli(version: str | None, dest: Path | None) -> None:
    version = version or get_version()
    if version == "unknown":
        click.echo(
            "Cannot determine installed runpod version; pass --version explicitly.",
            err=True,
        )
        sys.exit(1)

    target = dest or _default_install_path()

    try:
        installed_at = download_gpu_test_binary(version=version, dest=target)
    except (BinaryDownloadError, BinaryChecksumMismatch) as exc:
        click.echo(f"Failed to install gpu_test: {exc}", err=True)
        sys.exit(1)

    click.echo(f"Installed gpu_test at {installed_at}")
