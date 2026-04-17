"""
Verifies the built wheel excludes platform-specific binaries so it stays
universal (py3-none-any) and installable on Nix / non-glibc platforms.

Regression guard for https://github.com/runpod/runpod-python/issues/498.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory) -> Path:
    """Build the wheel once per test module and return its path."""
    out_dir = tmp_path_factory.mktemp("dist")
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    wheels = list(out_dir.glob("runpod-*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    return wheels[0]


def test_wheel_excludes_gpu_test_binary(built_wheel: Path) -> None:
    """The gpu_test ELF binary must NOT be bundled in the PyPI wheel."""
    with zipfile.ZipFile(built_wheel) as zf:
        names = zf.namelist()
    offending = [n for n in names if n.endswith("serverless/binaries/gpu_test")]
    assert offending == [], (
        f"wheel still contains the gpu_test binary: {offending}. "
        "See docs/serverless/gpu_binary_compilation.md for the opt-in install path."
    )


def test_wheel_is_universal(built_wheel: Path) -> None:
    """Filename tag must be py3-none-any (no platform pinning)."""
    assert built_wheel.name.endswith("-py3-none-any.whl"), (
        f"wheel is not universal: {built_wheel.name}"
    )
