"""Customer-safety regressions for automatic early worker checks."""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from runpod._health import fitness, system
from runpod._startup import run_import_checks


@pytest.mark.parametrize(
    "args",
    [
        ["handler.py"],
        ["handler.py", "--test_input", "{}"],
        ["handler.py", "--test_input={}"],
        ["handler.py", "--rp_serve_api"],
    ],
)
def test_import_does_not_run_for_unmarked_or_local_process(monkeypatch, args):
    monkeypatch.setenv("RUNPOD_WEBHOOK_GET_JOB", "https://example.test/job")
    monkeypatch.setattr(sys, "argv", args)
    if len(args) > 1:
        monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "endpoint")
    else:
        monkeypatch.delenv("RUNPOD_ENDPOINT_ID", raising=False)
    with patch.object(fitness, "run_startup_fitness_checks") as run:
        run_import_checks()
    run.assert_not_called()


def test_initial_pass_does_not_compare_config(monkeypatch):
    monkeypatch.setenv("RUNPOD_WEBHOOK_GET_JOB", "https://example.test/job")
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "endpoint")
    with patch.object(fitness, "_refresh_late_config") as refresh:
        run_import_checks()
    refresh.assert_not_called()


@pytest.mark.asyncio
async def test_registration_failure_rolls_back_and_reports_at_worker_start(monkeypatch):
    def partial_registration():
        fitness.register_fitness_check(lambda: None)
        raise ValueError("bad threshold")

    monkeypatch.setattr(
        fitness, "_ensure_system_checks_registered", partial_registration
    )
    await fitness.run_fitness_checks(include_deferred=False)
    assert fitness._fitness_checks == []
    assert fitness._registration_state == {"gpu_check": False, "system_checks": False}
    with patch.object(fitness, "_report_unhealthy") as report:
        with pytest.raises(SystemExit):
            await fitness.run_fitness_checks()
    assert report.call_args.args == ("fitness_check_setup", "ValueError: bad threshold")


@pytest.mark.asyncio
async def test_setup_failure_exits_even_if_reporting_breaks(monkeypatch):
    monkeypatch.setattr(
        fitness, "_register_builtins", MagicMock(side_effect=ValueError("bad"))
    )
    monkeypatch.setattr(
        fitness, "_report_unhealthy", MagicMock(side_effect=RuntimeError("offline"))
    )
    with pytest.raises(SystemExit) as exc:
        await fitness.run_fitness_checks()
    assert exc.value.code == 1


@pytest.mark.asyncio
async def test_network_retries_then_succeeds_on_worker_api_host(monkeypatch):
    monkeypatch.setenv(
        "RUNPOD_WEBHOOK_GET_JOB", "https://worker.example:8443/job?token=secret"
    )
    writer = MagicMock()
    writer.wait_closed = AsyncMock()
    with patch("asyncio.open_connection", new_callable=AsyncMock) as connect:
        connect.side_effect = [ConnectionRefusedError(), (MagicMock(), writer)]
        await system._check_network_connectivity()
    assert connect.await_count == 2
    connect.assert_awaited_with("worker.example", 8443)
    writer.close.assert_called_once()


@pytest.mark.asyncio
async def test_network_stuck_close_is_bounded(monkeypatch):
    monkeypatch.setattr(system, "NETWORK_CHECK_TIMEOUT", 0.1)
    writer = MagicMock()
    writer.wait_closed.side_effect = lambda: asyncio.sleep(60)
    with patch(
        "asyncio.open_connection",
        new_callable=AsyncMock,
        return_value=(MagicMock(), writer),
    ):
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="Timeout"):
            await system._check_network_connectivity()
    assert time.monotonic() - started < 1
    writer.transport.abort.assert_called()


def test_network_is_deferred_even_in_authorized_worker(monkeypatch):
    monkeypatch.delenv("RUNPOD_SKIP_AUTO_SYSTEM_CHECKS")
    monkeypatch.setenv("RUNPOD_WEBHOOK_GET_JOB", "https://worker.example/job")
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "endpoint")
    with (
        patch.object(system, "gpu_available", return_value=False),
        patch.object(system, "_check_memory_availability"),
        patch.object(system, "_check_disk_space"),
        patch.object(
            system, "_check_network_connectivity", new_callable=AsyncMock
        ) as network,
    ):
        run_import_checks()
    network.assert_not_awaited()
    assert any(c.__name__ == "_network_check" for c in fitness._fitness_checks)


def test_changed_threshold_is_applied_without_rerunning_unrelated_checks(monkeypatch):
    monkeypatch.delenv("RUNPOD_SKIP_AUTO_SYSTEM_CHECKS")
    monkeypatch.setenv("RUNPOD_WEBHOOK_GET_JOB", "https://worker.example/job")
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "endpoint")
    with (
        patch.object(system, "gpu_available", return_value=False),
        patch.object(system, "_check_memory_availability") as memory,
        patch.object(system, "_check_disk_space") as disk,
        patch.object(system, "_check_network_connectivity", new_callable=AsyncMock),
    ):
        run_import_checks()
        monkeypatch.setenv("RUNPOD_MIN_DISK_PERCENT", "2")
        asyncio.run(fitness.run_fitness_checks())
        assert system.MIN_DISK_PERCENT == 2
    assert memory.call_count == 1
    assert disk.call_count == 2


def test_passing_early_pass_marks_environment_for_children(monkeypatch):
    monkeypatch.setenv("RUNPOD_WEBHOOK_GET_JOB", "https://worker.example/job")
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "endpoint")
    run_import_checks()
    assert os.environ[fitness.EARLY_CHECKS_DONE_ENV] == "1"


def test_setup_failure_does_not_mark_environment(monkeypatch):
    monkeypatch.delenv("RUNPOD_SKIP_AUTO_SYSTEM_CHECKS")
    monkeypatch.setenv("RUNPOD_MIN_MEMORY_GB", "invalid")
    monkeypatch.setenv("RUNPOD_WEBHOOK_GET_JOB", "https://worker.example/job")
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "endpoint")
    run_import_checks()
    assert fitness.EARLY_CHECKS_DONE_ENV not in os.environ


def test_child_process_with_marker_skips_early_pass(monkeypatch):
    monkeypatch.setenv("RUNPOD_WEBHOOK_GET_JOB", "https://worker.example/job")
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "endpoint")
    monkeypatch.setenv(fitness.EARLY_CHECKS_DONE_ENV, "1")
    with patch.object(fitness, "run_fitness_checks") as run:
        run_import_checks()
    run.assert_not_called()


def test_spawned_child_inherits_marker_and_skips_probes(tmp_path):
    """A multiprocessing spawn child re-imports the handler; it must not re-probe."""
    # Spawn re-imports __main__ by path, so the script must live in a file.
    script = tmp_path / "handler.py"
    script.write_text("""
import multiprocessing, os, sys
from unittest.mock import patch
from runpod._health import fitness, system
from runpod._startup import run_import_checks

def child(queue):
    from runpod._health import fitness, system
    with patch.object(system, '_check_memory_availability') as memory:
        run_import_checks()
    queue.put((os.environ.get(fitness.EARLY_CHECKS_DONE_ENV), memory.call_count))

if __name__ == '__main__':
    os.environ['RUNPOD_WEBHOOK_GET_JOB'] = 'https://example.test/job'
    os.environ['RUNPOD_ENDPOINT_ID'] = 'endpoint'
    os.environ['RUNPOD_SKIP_AUTO_SYSTEM_CHECKS'] = 'false'
    with patch.object(system, 'gpu_available', return_value=False), \
         patch.object(system, '_check_memory_availability') as memory, \
         patch.object(system, '_check_disk_space'):
        run_import_checks()
    assert memory.call_count == 1
    ctx = multiprocessing.get_context('spawn')
    queue = ctx.Queue()
    proc = ctx.Process(target=child, args=(queue,))
    proc.start()
    marker, child_calls = queue.get(timeout=30)
    proc.join(30)
    assert marker == '1', marker
    assert child_calls == 0, child_calls
    print('SPAWN_PASS')
""")
    result = run_child(str(script), argv_mode="file", timeout=60)
    assert result.returncode == 0, result.stderr
    assert "SPAWN_PASS" in result.stdout


def run_child(code, argv_mode="-c", timeout=15, **kwargs):
    env = {k: v for k, v in os.environ.items() if not k.startswith("RUNPOD_")}
    env.update(RUNPOD_SKIP_GPU_CHECK="true", RUNPOD_SKIP_AUTO_SYSTEM_CHECKS="true")
    argv = [sys.executable, "-c", code] if argv_mode == "-c" else [sys.executable, code]
    return subprocess.run(
        argv,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        **kwargs,
    )


def test_actual_import_is_safe_with_inherited_worker_environment():
    result = run_child("""
import os
os.environ['RUNPOD_WEBHOOK_GET_JOB'] = 'https://example.test/job'
os.environ['RUNPOD_SKIP_AUTO_SYSTEM_CHECKS'] = 'false'
os.environ['RUNPOD_MIN_MEMORY_GB'] = 'invalid'
import runpod
print('IMPORT_SURVIVED')
""")
    assert result.returncode == 0, result.stderr
    assert "IMPORT_SURVIVED" in result.stdout


def test_setup_failure_exits_with_live_thread():
    result = run_child("""
import asyncio, os, threading, time
from runpod._health import fitness
os.environ['RUNPOD_SKIP_AUTO_SYSTEM_CHECKS'] = 'false'
os.environ['RUNPOD_MIN_MEMORY_GB'] = 'invalid'
threading.Thread(target=lambda: time.sleep(60), daemon=False).start()
asyncio.run(fitness.run_fitness_checks())
""")
    assert result.returncode == 1, result.stderr
    assert "fitness_check_setup" in result.stdout


def test_lazy_parent_early_checks_never_import_serverless_or_cuda_libraries():
    root = str(Path(__file__).resolve().parents[5] / "runpod")
    result = run_child(f"""
import asyncio, importlib.abc, os, sys, types
# Model the apps-sdk lazy package: no eager serverless import.
package = types.ModuleType('runpod')
package.__path__ = [{root!r}]
sys.modules['runpod'] = package
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('runpod.serverless') or fullname.split('.')[0] in ('torch', 'cupy'):
            raise AssertionError('early check loaded ' + fullname)
sys.meta_path.insert(0, Guard())
from unittest.mock import patch, MagicMock
from runpod._health import fitness, system
from runpod._startup import run_import_checks
os.environ['RUNPOD_WEBHOOK_GET_JOB'] = 'https://example.test/job'
os.environ['RUNPOD_ENDPOINT_ID'] = 'endpoint'
os.environ['RUNPOD_SKIP_AUTO_SYSTEM_CHECKS'] = 'false'
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
with patch.object(system, 'gpu_available', return_value=False), patch.object(system, '_check_memory_availability'), patch.object(system, '_check_disk_space'):
    run_import_checks()
assert asyncio.get_event_loop() is loop
loop.close()
assert sorted(c.__name__ for c in fitness._completed_checks) == ['_disk_check', '_memory_check']
os.environ['RUNPOD_WEBHOOK_PING'] = 'https://example.test/ping'
os.environ['RUNPOD_AI_API_KEY'] = 'fake-test-key'
with patch('requests.Session') as session:
    fitness._report_unhealthy('test', 'failure')
    session.return_value.get.assert_called_once()
print('LAZY_PASS')
""")
    assert result.returncode == 0, result.stderr
    assert "LAZY_PASS" in result.stdout
