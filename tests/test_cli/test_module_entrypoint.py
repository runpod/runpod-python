"""module entry points dispatch command-line arguments."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", ["runpod.cli.main", "runpod.rp_cli.main"])
@pytest.mark.parametrize(
    "arguments, expected",
    [(["--help"], "Usage:"), (["completion", "bash"], "_rp_completion")],
)
def test_module_entrypoint(module, arguments, expected, tmp_path):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", module, *arguments],
        cwd=root,
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "PYTHONPATH": str(root),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout
    assert "Traceback" not in result.stderr
