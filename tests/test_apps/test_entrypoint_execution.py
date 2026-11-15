"""execution and loop ownership for local entrypoints."""

import asyncio
import subprocess
import sys
import textwrap

import pytest

from runpod.apps.entrypoint import run_entrypoint


def test_sync_entrypoint_returns_result():
    assert run_entrypoint(lambda: "done") == "done"


def test_sync_entrypoint_returning_awaitable():
    class Result:
        def __await__(self):
            return asyncio.sleep(0, result="done").__await__()

    assert run_entrypoint(Result) == "done"


@pytest.mark.parametrize("fails", [False, True])
def test_async_entrypoint_closes_its_loop_and_pending_tasks(fails):
    loops = []
    cleaned = []

    async def pending(started):
        try:
            started.set()
            await asyncio.Future()
        finally:
            await asyncio.sleep(0)
            cleaned.append(True)

    async def main():
        loops.append(asyncio.get_running_loop())
        started = asyncio.Event()
        asyncio.create_task(pending(started))
        await started.wait()
        if fails:
            raise ValueError("entrypoint failed")
        return "done"

    for _ in range(2):
        if fails:
            with pytest.raises(ValueError, match="entrypoint failed"):
                run_entrypoint(main)
        else:
            assert run_entrypoint(main) == "done"

    assert loops[0] is not loops[1]
    assert all(loop.is_closed() for loop in loops)
    assert cleaned == [True, True]


@pytest.mark.parametrize("via_cli", [False, True])
def test_async_entrypoint_allows_sync_sdk_calls(via_cli, tmp_path):
    module = tmp_path / "main.py"
    module.write_text(textwrap.dedent('''\
        import asyncio
        import runpod
        from runpod.apps.context import block

        app = runpod.App("entrypoint-test")
        queue = runpod.Queue(app="entrypoint-test", name="queue")
        api = runpod.Api(app="entrypoint-test", name="api")

        @app.queue(name="worker")
        def worker(value):
            return value

        @runpod.local_entrypoint
        async def main():
            loop = asyncio.get_running_loop()
            assert block(asyncio.sleep(0, result="done")) == "done"
            assert worker.remote("hello") == "done"
            assert queue.remote(value="hello") == "done"
            assert queue.spawn(value="hello").id == "job-1"
            assert api.get("/health") == {"healthy": True}
            assert await queue.remote.aio(value="hello") == "done"
            assert asyncio.get_running_loop() is loop
            print("entrypoint completed")
            return "done"
        '''))
    script = '''
import importlib.util
import sys
from unittest.mock import AsyncMock, patch
from click.testing import CliRunner
from runpod.apps.entrypoint import run_entrypoint
from runpod.apps.targets import SentinelTarget
from runpod.rp_cli.main import cli

async def start(session):
    for app in session.apps:
        for name in app.resources:
            app._dev_targets[name] = SentinelTarget(app.name, app.env, name)

with (
    patch.object(SentinelTarget, "invoke", AsyncMock(return_value="done")),
    patch.object(SentinelTarget, "submit", AsyncMock(return_value={"id": "job-1"})),
    patch.object(SentinelTarget, "request", AsyncMock(return_value={"healthy": True})),
    patch("runpod.apps.dev.DevSession.start", start),
    patch("runpod.apps.dev.DevSession.stop", AsyncMock()) as stop,
):
    if VIA_CLI:
        result = CliRunner().invoke(cli, ["flash", "dev", sys.argv[1], "--once"])
        assert result.exit_code == 0, (result.output, result.exception)
        assert "entrypoint completed" in result.output
        stop.assert_awaited_once()
    else:
        spec = importlib.util.spec_from_file_location("entrypoint_test", sys.argv[1])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert run_entrypoint(module.main) == "done"
'''
    result = subprocess.run(
        [sys.executable, "-c", f"VIA_CLI = {via_cli!r}\n" + script, str(module)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
