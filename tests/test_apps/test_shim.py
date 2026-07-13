"""tests for the shell launcher shim."""

import base64
import subprocess

from runpod.apps.shim import shell_launcher


def test_no_inner_single_quotes():
    # the host lexes dockerArgs; an inner single quote would break the
    # outer quoting
    cmd = shell_launcher("VAR", "/dest.py")
    assert cmd.startswith("sh -c '")
    assert cmd.endswith("'")
    inner = cmd[len("sh -c '") : -1]
    assert "'" not in inner


def test_posix_sh_not_bash():
    cmd = shell_launcher("VAR", "/dest.py")
    assert cmd.startswith("sh -c "), "must not require bash"


def test_decodes_and_execs_locally(tmp_path):
    """run the inner script under sh with a payload that writes a marker."""
    marker = tmp_path / "marker"
    payload = f"open({str(marker)!r}, 'w').write('ran')"
    b64 = base64.b64encode(payload.encode()).decode()

    dest = tmp_path / "injected.py"
    cmd = shell_launcher("TESTVAR", str(dest))
    inner = cmd[len("sh -c '") : -1]

    result = subprocess.run(
        ["sh", "-c", inner],
        env={"TESTVAR": b64, "PATH": "/usr/bin:/bin:/usr/local/bin"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert marker.read_text() == "ran"


def test_probes_beyond_path():
    cmd = shell_launcher("VAR", "/dest.py")
    assert "/opt/conda/bin/python" in cmd
    assert "/opt/venv/bin/python" in cmd


def test_pythonless_image_fails_loudly():
    cmd = shell_launcher("VAR", "/dest.py")
    assert "FATAL" in cmd
    assert "must include python3" in cmd
