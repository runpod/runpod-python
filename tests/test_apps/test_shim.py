"""tests for the custom-image runtime launcher."""

import os
import subprocess
import sys

import pytest

from runpod.apps.shim import runtime_launcher


def test_starts_an_installed_runtime(tmp_path):
    marker = tmp_path / "marker"
    package = tmp_path / "runpod_sdk_runtime" / "task"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (tmp_path / "runpod.py").write_text("")
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



def test_rejects_unknown_runtime_kind():
    with pytest.raises(ValueError, match="unknown runtime kind"):
        runtime_launcher("other")


@pytest.mark.parametrize(
    ("installed", "runtime_override", "sdk_override", "expected_installs"),
    [
        (False, "", "", ["runpod-sdk-runtime", "runpod"]),
        (True, "", "", []),
        (True, "runpod-sdk-runtime==2.1", "runpod==3.2", [
            "runpod-sdk-runtime==2.1", "runpod==3.2"
        ]),
    ],
)
def test_installs_missing_packages_and_honors_overrides(
    tmp_path, installed, runtime_override, sdk_override, expected_installs
):
    import json
    import shlex

    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "runtime": installed,
        "sdk": installed,
        "installs": [],
    }))
    interpreter = tmp_path / "python3"
    interpreter.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "path = Path(os.environ['SHIM_STATE'])\n"
        "state = json.loads(path.read_text())\n"
        "args = sys.argv[1:]\n"
        "if args[0] == '-c':\n"
        "    key = 'runtime' if args[1] == 'import runpod_sdk_runtime' else 'sdk'\n"
        "    sys.exit(0 if state[key] else 1)\n"
        "if args[:2] == ['-m', 'pip']:\n"
        "    spec = args[-1]\n"
        "    state['installs'].append(spec)\n"
        "    state['runtime' if spec.startswith('runpod-sdk-runtime') else 'sdk'] = True\n"
        "else:\n"
        "    if not (state['runtime'] and state['sdk']):\n"
        "        sys.exit(2)\n"
        "    state['started'] = args[1]\n"
        "path.write_text(json.dumps(state))\n"
    )
    interpreter.chmod(0o755)
    result = subprocess.run(
        shlex.split(runtime_launcher("queue")),
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "SHIM_STATE": str(state_path),
            "RUNPOD_RUNTIME_PACKAGE_SPEC": runtime_override,
            "RUNPOD_PACKAGE_SPEC": sdk_override,
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    state = json.loads(state_path.read_text())
    assert state["installs"] == expected_installs
    assert state["started"] == "runpod_sdk_runtime.bootstrap"
