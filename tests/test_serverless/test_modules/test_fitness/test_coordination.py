"""Real interprocess locks/results, and container-start identity regressions."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from runpod._health import coordination, fitness, is_early_check_eligible
from runpod._health.coordination import container_start_id as real_container_start_id


@pytest.mark.parametrize(
    "endpoint,webhook,test,expected",
    [
        ("", "", "", False),
        ("ep", "", "", False),
        ("", "https://example.test/job", "", False),
        ("ep", "https://example.test/job", "", True),
        ("ep", "https://example.test/job", "TRUE", False),
        ("ep", "https://example.test/job", "1", False),
    ],
)
def test_environment_gate(monkeypatch, endpoint, webhook, test, expected):
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", endpoint)
    monkeypatch.setenv("RUNPOD_WEBHOOK_GET_JOB", webhook)
    monkeypatch.setenv("RUNPOD_TEST", test)
    monkeypatch.setattr(sys, "argv", ["handler.py"])
    assert is_early_check_eligible() is expected


def process_code(tmp_path, fail=False):
    return f"""
import asyncio, os, time
from pathlib import Path
from runpod._health import fitness, coordination
coordination.container_start_id = lambda: {(tmp_path.parent.name + "-" + tmp_path.name)!r}
os.environ['RUNPOD_ENDPOINT_ID'] = 'ep'
os.environ['RUNPOD_WEBHOOK_GET_JOB'] = 'https://example.test/job'
fitness._report_unhealthy = lambda *args: None
@fitness.register_fitness_check
def shared_check():
    with open({str(tmp_path / "calls")!r}, 'a') as out:
        out.write('check\\n')
    time.sleep(0.2)
    if {fail!r}:
        raise RuntimeError('failed')
shared_check._runpod_builtin = 'system_checks'
asyncio.run(fitness.run_fitness_checks(include_deferred=False))
asyncio.run(fitness.run_fitness_checks())
"""


def launch(code):
    env = {k: v for k, v in os.environ.items() if not k.startswith("RUNPOD_")}
    env.update(RUNPOD_SKIP_GPU_CHECK="true", RUNPOD_SKIP_AUTO_SYSTEM_CHECKS="true")
    return subprocess.Popen(
        [sys.executable, "-c", code],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_independent_processes_share_success(tmp_path):
    children = [launch(process_code(tmp_path)) for _ in range(3)]
    for child in children:
        out, err = child.communicate(timeout=10)
        assert child.returncode == 0, out + err
    assert (tmp_path / "calls").read_text().splitlines() == ["check"]


def test_failure_propagates_without_rerunning(tmp_path):
    for _ in range(2):
        child = launch(process_code(tmp_path, fail=True))
        out, err = child.communicate(timeout=10)
        assert child.returncode == 1, out + err
    assert (tmp_path / "calls").read_text().splitlines() == ["check"]


@pytest.mark.asyncio
async def test_busy_is_bounded_and_owner_crash_releases_lock(tmp_path):
    code = f"""
import asyncio, time
from runpod._health import coordination
coordination.container_start_id = lambda: {(tmp_path.parent.name + "-" + tmp_path.name)!r}
async def main():
    async with coordination.ContainerChecks():
        print('LOCKED', flush=True)
        time.sleep(30)
asyncio.run(main())
"""
    child = launch(code)
    try:
        assert child.stdout.readline().strip() == "LOCKED"
        with pytest.raises(coordination.CoordinationBusy):
            async with coordination.ContainerChecks(timeout=0.1):
                pass
    finally:
        child.kill()
        child.communicate(timeout=5)
    async with coordination.ContainerChecks(timeout=0.1) as shared:
        assert shared.state["passed"] == []


@pytest.mark.asyncio
async def test_restart_does_not_reuse_previous_success(monkeypatch, tmp_path):
    async with coordination.ContainerChecks() as shared:
        shared.state["passed"] = ["old-success"]
        shared.save()
    monkeypatch.setattr(
        coordination,
        "container_start_id",
        lambda: (tmp_path.parent.name + "-" + tmp_path.name) + "-restart",
    )
    async with coordination.ContainerChecks() as shared:
        assert shared.state["passed"] == []


def test_identity_changes_when_init_restarts(monkeypatch):
    # Fields following comm start at field 3; starttime is field 22.
    ticks = ["100"]

    def read(path):
        if str(path).endswith("boot_id"):
            return "host-boot"
        return "1 (name with spaces) " + " ".join(["0"] * 19 + ticks)

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(os, "readlink", lambda path: "pid:[123]")
    first = real_container_start_id()
    ticks[0] = "200"
    assert real_container_start_id() != first


@pytest.mark.asyncio
async def test_unavailable_coordination_defers_then_checks_at_start(monkeypatch):
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep")
    monkeypatch.setenv("RUNPOD_WEBHOOK_GET_JOB", "https://example.test/job")

    def unavailable():
        raise OSError("read-only")

    monkeypatch.setattr(coordination, "container_start_id", unavailable)
    calls = []

    @fitness.register_fitness_check
    def early():
        calls.append("early")

    early._runpod_builtin = "system_checks"

    @fitness.register_fitness_check
    def customer():
        calls.append("customer")

    await fitness.run_fitness_checks(include_deferred=False)
    assert calls == []
    await fitness.run_fitness_checks()
    assert calls == ["early", "customer"]


@pytest.mark.asyncio
async def test_customer_and_process_checks_only_at_start(monkeypatch):
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep")
    monkeypatch.setenv("RUNPOD_WEBHOOK_GET_JOB", "https://example.test/job")
    calls = []

    @fitness.register_fitness_check
    def customer():
        calls.append("customer")

    @fitness.register_fitness_check
    @fitness.defer_to_worker_start
    def cuda():
        calls.append("cuda")

    cuda._runpod_builtin = "system_checks"
    await fitness.run_fitness_checks(include_deferred=False)
    assert calls == []
    await fitness.run_fitness_checks()
    assert calls == ["customer", "cuda"]


@pytest.mark.asyncio
@pytest.mark.parametrize("contents", ["[]", '{"passed": null}', "{broken"])
async def test_corrupt_state_uses_fallback(tmp_path, contents):
    identity = coordination.container_start_id()
    Path(f"/tmp/runpod-fitness-{identity}.json").write_text(contents)
    with pytest.raises(coordination.CoordinationUnavailable):
        async with coordination.ContainerChecks():
            pass


@pytest.mark.asyncio
async def test_lock_timeout_defers_import_but_blocks_worker(monkeypatch):
    class Busy:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            raise coordination.CoordinationBusy("still checking")

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(fitness, "ContainerChecks", Busy)
    monkeypatch.setattr(fitness, "_report_unhealthy", lambda *args: None)
    await fitness._run_shared_checks(include_deferred=False)
    with pytest.raises(SystemExit):
        await fitness._run_shared_checks(include_deferred=True)


@pytest.mark.asyncio
async def test_deferred_worker_wait_covers_long_gpu_check(monkeypatch):
    from runpod._health import gpu, system

    monkeypatch.delenv("RUNPOD_SKIP_GPU_CHECK")
    monkeypatch.delenv("RUNPOD_SKIP_AUTO_SYSTEM_CHECKS")
    monkeypatch.setenv("RUNPOD_DEFER_FITNESS_CHECKS", "true")
    monkeypatch.setenv("RUNPOD_GPU_TEST_TIMEOUT", "60")
    monkeypatch.setattr(gpu, "TIMEOUT_SECONDS", gpu.TIMEOUT_SECONDS)
    monkeypatch.setattr(gpu, "MAX_ERROR_MESSAGES", gpu.MAX_ERROR_MESSAGES)
    gpu.configure()
    waits = []

    class ConcurrentCheck:
        def __init__(self, timeout):
            waits.append(timeout)
            self.state = {"passed": [], "failure": None}

        async def __aenter__(self):
            # Model an owner finishing after 45 seconds without a slow test.
            if waits[-1] < 45:
                raise coordination.CoordinationBusy("healthy check still running")
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(fitness, "ContainerChecks", ConcurrentCheck)
    await fitness._run_shared_checks(include_deferred=True)
    assert waits == [
        60 + gpu.FALLBACK_TIMEOUT_SECONDS + 2 * system.CUDA_VERSION_PROBE_TIMEOUT + 5
    ]
    # Imports retain a short bounded wait and defer rather than terminating.
    await fitness._run_shared_checks(include_deferred=False)
    assert waits[-1] == 35
