"""cli output on streams with limited character encodings."""

import io
import os
import subprocess
import sys
import textwrap

import pytest

from runpod.rp_cli import main


@pytest.mark.parametrize("encoding", ["cp1252", "ascii", "utf-8"])
@pytest.mark.parametrize("destination", ["pipe", "file"])
@pytest.mark.parametrize("build_only", [False, True])
def test_redirected_deploy_output(tmp_path, encoding, destination, build_only):
    (tmp_path / "main.py").write_text(
        'import runpod\napp = runpod.App("demo")\n'
        '@app.queue(gpu="4090")\ndef chat():\n    return "hello"\n',
        encoding="utf-8",
    )
    script = textwrap.dedent("""
        from pathlib import Path
        from unittest.mock import patch

        from runpod.apps.deploy import DeployResult
        from runpod.rp_cli.main import run

        def build(app, root, *, events, output, **kwargs):
            events.phase("package")
            output.write_bytes(b"artifact")
            return output

        async def deploy(app, root, *, events, **kwargs):
            events.phase("package")
            events.phase("upload")
            events.upload_progress(1024, 1024)
            events.phase("endpoints")
            Path("deployed").touch()
            return DeployResult(
                app_name="demo", build_id="b1", environment_id="env1",
                resources=["chat"], endpoints={"chat": "ep1"},
            )

        with (
            patch("runpod.apps.deploy.build_artifact", side_effect=build),
            patch("runpod.apps.deploy.deploy_app", side_effect=deploy),
            patch("runpod.rp_cli.update.start_background_check"),
        ):
            run()
        """)
    command = [
        sys.executable,
        "-c",
        script,
        "flash",
        "deploy",
        "--python-version",
        "3.12",
    ]
    if build_only:
        command.append("--build-only")
    env = {**os.environ, "PYTHONIOENCODING": f"{encoding}:strict", "PYTHONUTF8": "0"}
    output_path = tmp_path / "out.txt"
    with output_path.open("wb") as output_file:
        result = subprocess.run(
            command,
            cwd=tmp_path,
            env=env,
            stdout=output_file if destination == "file" else subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
    output = (
        output_path.read_bytes() if destination == "file" else result.stdout
    ).decode(encoding)
    assert result.returncode == 0, output
    assert "packaging artifact" in output
    assert "UnicodeEncodeError" not in output
    check = "✓" if encoding == "utf-8" else "?"
    if build_only:
        assert (tmp_path / "demo-artifact.tar.gz").read_bytes() == b"artifact"
        assert f"{check} built demo" in output
    else:
        assert (tmp_path / "deployed").exists()
        assert f"{check} demo/default is live" in output
        arrow = "→" if encoding == "utf-8" else "?"
        assert f"{arrow} default" in output
        assert "ep1" in output


@pytest.mark.parametrize("encoding", ["cp1252", "ascii", "utf-8"])
def test_run_configures_both_streams(monkeypatch, encoding):
    with (
        io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="strict") as stdout,
        io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="strict") as stderr,
    ):
        monkeypatch.setattr(sys, "stdout", stdout)
        monkeypatch.setattr(sys, "stderr", stderr)

        def command():
            for stream in (sys.stdout, sys.stderr):
                stream.write("✓ → café\n")
                stream.flush()

        monkeypatch.setattr(main, "cli", command)
        main.run()

        expected = "✓ → café\n".encode(encoding, errors="replace")
        for stream in (stdout, stderr):
            assert stream.buffer.getvalue() == expected
            assert stream.encoding == encoding


def test_run_accepts_capture_streams(monkeypatch):
    stdout, stderr = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(main, "cli", lambda: print("✓"))

    main.run()

    assert stdout.getvalue() == "✓\n"
