"""tests for the custom-image runtime launcher."""

import os
import subprocess
import sys

import pytest

from runpod.apps.shim import runtime_launcher


def test_no_inner_single_quotes():
    command = runtime_launcher("queue")
    assert command.startswith("sh -c '")
    assert command.endswith("'")
    assert "'" not in command[len("sh -c '") : -1]


def test_posix_sh_not_bash():
    assert runtime_launcher("queue").startswith("sh -c ")


def test_starts_an_installed_runtime(tmp_path):
    marker = tmp_path / "marker"
    package = tmp_path / "runpod_sdk_runtime" / "task"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "runner.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n"
    )

    command = runtime_launcher("task")
    inner = command[len("sh -c '") : -1]
    result = subprocess.run(
        ["sh", "-c", inner],
        env={
            "PATH": f"{os.path.dirname(sys.executable)}:/usr/bin:/bin",
            "PYTHONPATH": str(tmp_path),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text() == "ran"


def test_probes_beyond_path():
    command = runtime_launcher("api")
    assert "/opt/conda/bin/python" in command
    assert "/opt/venv/bin/python" in command


def test_pythonless_image_fails_loudly():
    command = runtime_launcher("queue")
    assert "FATAL" in command
    assert "must include python3" in command


def test_supports_runtime_and_sdk_package_overrides():
    command = runtime_launcher("queue")
    assert "RUNPOD_RUNTIME_PACKAGE_SPEC" in command
    assert "RUNPOD_PACKAGE_SPEC" in command
    assert "runpod-sdk-runtime" in command


def test_rejects_unknown_runtime_kind():
    with pytest.raises(ValueError, match="unknown runtime kind"):
        runtime_launcher("other")
