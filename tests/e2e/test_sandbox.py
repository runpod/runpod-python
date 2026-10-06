import asyncio
import os
import time

import pytest

from runpod import AsyncioSandbox, Sandbox
from runpod.sandbox import SandboxExecutionError


async def _read_with_borrowed_async(sandbox_id, connection_options):
    async with await AsyncioSandbox.get(sandbox_id, **connection_options) as borrowed:
        result = await borrowed.exec(
            ["python", "-c", "print('borrowed async')"],
            check=True,
            timeout_seconds=4,
        )
        assert result.output.strip() == "borrowed async"


def test_sandbox_execution_and_borrowed_handles(require_api_key):
    connection_options = {
        "api_key": os.environ["RUNPOD_API_KEY"],
        "request_timeout": 30,
        "startup_timeout": 90,
    }
    with Sandbox(
        image="python:3.12-slim",
        cmd=["sleep", "infinity"],
        vcpu_count=2,
        memory_in_gb=4,
        idle_timeout_seconds=180,
        max_lifetime_seconds=300,
        **connection_options,
    ) as sandbox:
        result = sandbox.exec(
            ["python", "-c", "print('foreground ready')"],
            check=True,
            timeout_seconds=4,
        )
        assert result.output.strip() == "foreground ready"

        with pytest.raises(SandboxExecutionError) as failure:
            sandbox.exec(
                ["python", "-c", "print('expected failure', flush=True); raise SystemExit(17)"],
                check=True,
                timeout_seconds=4,
            )
        assert failure.value.sandbox_id == sandbox.id
        assert "expected failure" in failure.value.result.output

        sandbox.exec(
            [
                "python",
                "-c",
                "import pathlib, time; time.sleep(6); "
                "pathlib.Path('/tmp/runpod-sandbox-e2e').write_text('detached complete')",
            ],
            check=True,
            timeout_seconds=15,
            background=True,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = sandbox.exec(
                [
                    "python",
                    "-c",
                    "import pathlib; path = pathlib.Path('/tmp/runpod-sandbox-e2e'); "
                    "print(path.read_text() if path.exists() else 'pending')",
                ],
                check=True,
                timeout_seconds=4,
                startup_timeout=0,
            )
            if result.output.strip() == "detached complete":
                break
            assert result.output.strip() == "pending", result
            time.sleep(1)
        else:
            pytest.fail("detached command did not complete within 30 seconds")

        with Sandbox.get(sandbox.id, **connection_options) as borrowed:
            result = borrowed.exec(
                ["python", "-c", "print('borrowed sync')"],
                check=True,
                timeout_seconds=4,
            )
            assert result.output.strip() == "borrowed sync"

        asyncio.run(_read_with_borrowed_async(sandbox.id, connection_options))
        result = sandbox.exec(
            ["python", "-c", "print('owner still running')"], check=True
        )
        assert result.output.strip() == "owner still running"

    with Sandbox.get(sandbox.id, **connection_options) as terminated:
        assert terminated.info.state == "TERMINATED"
