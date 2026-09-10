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
from runpod._startup import WORKER_PID_ENV, run_import_checks


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
        monkeypatch.setenv(WORKER_PID_ENV, str(os.getpid()))
    else:
        monkeypatch.delenv(WORKER_PID_ENV, raising=False)
    with patch.object(fitness, "run_startup_fitness_checks") as run:
        run_import_checks()
    run.assert_not_called()


def test_inherited_worker_pid_does_not_authorize_child(monkeypatch):
    monkeypatch.setenv("RUNPOD_WEBHOOK_GET_JOB", "https://example.test/job")
    monkeypatch.setenv(WORKER_PID_ENV, str(os.getpid() + 1))
    with patch.object(fitness, "run_startup_fitness_checks") as run:
        run_import_checks()
    run.assert_not_called()


def test_initial_pass_does_not_compare_config(monkeypatch):
    monkeypatch.setenv("RUNPOD_WEBHOOK_GET_JOB", "https://example.test/job")
    monkeypatch.setenv(WORKER_PID_ENV, str(os.getpid()))
    with patch.object(fitness, "_refresh_late_config") as refresh:
        run_import_checks()
    refresh.assert_not_called()
    assert WORKER_PID_ENV not in os.environ


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
    monkeypatch.setenv(WORKER_PID_ENV, str(os.getpid()))
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
    monkeypatch.setenv(WORKER_PID_ENV, str(os.getpid()))
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


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.asyncio
async def test_realtime_checks_before_serving_but_local_api_exempt(monkeypatch, local):
    from runpod.serverless.modules.rp_fastapi import WorkerAPI

    monkeypatch.setenv("RUNPOD_REALTIME_PORT", "8000")
    monkeypatch.setenv("RUNPOD_WEBHOOK_GET_JOB", "https://worker.example/job")
    api = object.__new__(WorkerAPI)
    api.config = {"rp_args": {"rp_serve_api": local}}
    with patch.object(fitness, "run_fitness_checks", new_callable=AsyncMock) as run:
        async with api._lifespan(None):
            assert run.await_count == (0 if local else 1)


def run_child(code, **kwargs):
    env = {k: v for k, v in os.environ.items() if not k.startswith("RUNPOD_")}
    env.update(RUNPOD_SKIP_GPU_CHECK="true", RUNPOD_SKIP_AUTO_SYSTEM_CHECKS="true")
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
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


@pytest.mark.parametrize("module_mode", [False, True])
def test_launcher_checks_before_handler_and_preserves_arguments(tmp_path, module_mode):
    handler = tmp_path / "handler.py"
    handler.write_text(
        "import os, sys\nassert 'RUNPOD_FITNESS_WORKER_PID' not in os.environ\n"
        "assert sys.argv[1:] == ['--customer-arg', 'value']\nprint('MODEL_LOAD')\n"
    )
    result = run_child(f"""
import os, sys
import runpod._worker_bootstrap as bootstrap
from runpod._health import fitness
os.environ['RUNPOD_WEBHOOK_GET_JOB'] = 'https://example.test/job'
fitness.register_fitness_check(lambda: print('EARLY_CHECK'))
os.chdir({str(tmp_path)!r})
# Model console entrypoint sys.path: the current directory is not pre-added.
sys.path = [p for p in sys.path if p]
sys.argv = ['runpod-worker'] + {(["-m", "handler"] if module_mode else [str(handler)])!r} + ['--customer-arg', 'value']
bootstrap.main()
""")
    assert result.returncode == 0, result.stderr
    assert result.stdout.index("EARLY_CHECK") < result.stdout.index("MODEL_LOAD")


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
os.environ['RUNPOD_FITNESS_WORKER_PID'] = str(os.getpid())
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


@pytest.mark.parametrize("method", ["spawn", "fork"])
def test_child_processes_do_not_repeat_early_checks(tmp_path, method):
    import multiprocessing

    if method not in multiprocessing.get_all_start_methods():
        pytest.skip(f"{method} is not supported")
    handler = tmp_path / "child_handler.py"
    handler.write_text("""
import multiprocessing, os
from runpod._startup import is_worker_process

def child():
    assert not is_worker_process()
    print('CHILD_SAFE', flush=True)

if __name__ == '__main__':
    child_process = multiprocessing.get_context(os.environ['TEST_START_METHOD']).Process(target=child)
    child_process.start()
    child_process.join(5)
    assert child_process.exitcode == 0
""")
    result = run_child(f"""
import os, sys
from runpod._worker_bootstrap import main
os.environ['TEST_START_METHOD'] = {method!r}
os.environ['RUNPOD_WEBHOOK_GET_JOB'] = 'https://example.test/job'
sys.argv = ['runpod-worker', {str(handler)!r}]
main()
""")
    assert result.returncode == 0, result.stderr
    assert "CHILD_SAFE" in result.stdout


def test_launcher_failed_early_check_prevents_model_load(tmp_path):
    handler = tmp_path / "handler.py"
    handler.write_text("print('MODEL_LOAD')\n")
    result = run_child(f"""
import os, sys
from runpod._worker_bootstrap import main
from runpod._health import fitness
os.environ['RUNPOD_WEBHOOK_GET_JOB'] = 'https://example.test/job'
def fail():
    raise RuntimeError('broken hardware')
fitness.register_fitness_check(fail)
sys.argv = ['runpod-worker', {str(handler)!r}]
main()
""")
    assert result.returncode == 1
    assert "broken hardware" in result.stdout
    assert "MODEL_LOAD" not in result.stdout


def test_launcher_local_test_does_not_run_early_checks(tmp_path):
    handler = tmp_path / "handler.py"
    handler.write_text("print('LOCAL_TEST')\n")
    result = run_child(f"""
import os, sys
from runpod._worker_bootstrap import main
from runpod._health import fitness
os.environ['RUNPOD_WEBHOOK_GET_JOB'] = 'https://example.test/job'
def fail():
    raise RuntimeError('must not run')
fitness.register_fitness_check(fail)
sys.argv = ['runpod-worker', {str(handler)!r}, '--test_input={{}}']
main()
""")
    assert result.returncode == 0, result.stderr
    assert "LOCAL_TEST" in result.stdout
