"""tests for the task runner and pod task transport."""

# the sync bridge finalizes coroutines on a background loop thread;
# AsyncMock coroutines observed there trip the unraisable checker
# non-deterministically under full-suite gc timing
import pytest as _pytest

pytestmark = _pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning"
)

import base64
import json
from unittest.mock import AsyncMock, patch

import cloudpickle
import pytest

import runpod
from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.errors import RemoteExecutionError
from runpod.apps.spec import ResourceKind, ResourceSpec
from runpod.runtimes.task.runner import execute_request
from runpod.apps.tasks import _pod_input, unwrap_task_response
from runpod.apps.targets import PodTarget


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


def _b64(value):
    return base64.b64encode(cloudpickle.dumps(value)).decode("utf-8")


def _unb64(value):
    return cloudpickle.loads(base64.b64decode(value))


class TestExecuteRequest:
    def test_simple_function(self):
        response = execute_request(
            {
                "function_name": "add",
                "function_code": "def add(a, b):\n    return a + b",
                "args": [_b64(2), _b64(3)],
                "kwargs": {},
            }
        )
        assert response["success"] is True
        assert _unb64(response["result"]) == 5

    def test_json_format(self):
        response = execute_request(
            {
                "function_name": "add",
                "function_code": "def add(a, b):\n    return a + b",
                "args": [2, 3],
                "kwargs": {},
                "serialization_format": "json",
            }
        )
        assert response["success"] is True
        assert response["json_result"] == 5

    def test_async_function(self):
        response = execute_request(
            {
                "function_name": "hello",
                "function_code": (
                    "async def hello(name):\n    return f'hi {name}'"
                ),
                "args": [],
                "kwargs": {"name": _b64("world")},
            }
        )
        assert response["success"] is True
        assert _unb64(response["result"]) == "hi world"

    def test_stdout_captured(self):
        response = execute_request(
            {
                "function_name": "loud",
                "function_code": "def loud():\n    print('noise')\n    return 1",
                "args": [],
                "kwargs": {},
                "serialization_format": "json",
            }
        )
        assert response["success"] is True
        assert "noise" in response["stdout"]

    def test_exception_reported(self):
        response = execute_request(
            {
                "function_name": "boom",
                "function_code": "def boom():\n    raise ValueError('bad')",
                "args": [],
                "kwargs": {},
            }
        )
        assert response["success"] is False
        assert "ValueError" in response["error"]

    def test_missing_function(self):
        response = execute_request(
            {
                "function_name": "nope",
                "function_code": "x = 1",
                "args": [],
                "kwargs": {},
            }
        )
        assert response["success"] is False
        assert "not found" in response["error"]


class TestPodInput:
    def _spec(self, **kw):
        defaults = dict(kind=ResourceKind.TASK, name="t")
        defaults.update(kw)
        return ResourceSpec(**defaults)

    def test_cpu_pod_input(self):
        pod = _pod_input(self._spec(cpu=["cpu3c-1-2"]), "tok", "t")
        assert pod["instanceIds"] == ["cpu3c-1-2"]
        assert pod["imageName"] == "runpod/task:py3.12-latest"
        assert pod["ports"] == "8080/http"
        assert pod["terminateAfter"]
        env = {e["key"]: e["value"] for e in pod["env"]}
        assert env["RUNPOD_TASK_TOKEN"] == "tok"
        # runtime images have the runner baked in; no env payload
        assert "RUNPOD_TASK_RUNNER_B64" not in env
        assert "dockerArgs" not in pod

    def test_gpu_pod_input(self):
        pod = _pod_input(
            self._spec(gpu=["NVIDIA GeForce RTX 4090"], gpu_count=2), "tok", "t"
        )
        assert pod["gpuTypeIdList"] == ["NVIDIA GeForce RTX 4090"]
        assert pod["gpuCount"] == 2
        assert pod["imageName"] == "runpod/task-gpu:latest"

    def test_custom_image_bootstraps_runner(self):
        pod = _pod_input(
            self._spec(cpu=["cpu3c-1-2"], image="my/image:1", volume="vol-1"),
            "tok",
            "t",
        )
        assert pod["imageName"] == "my/image:1"
        assert pod["networkVolumeId"] == "vol-1"
        # custom images get the env-injection fallback
        env = {e["key"]: e["value"] for e in pod["env"]}
        assert "RUNPOD_TASK_RUNNER_B64" in env
        decoded = base64.b64decode(env["RUNPOD_TASK_RUNNER_B64"]).decode()
        assert "def execute_request" in decoded
        assert "task_runner.py" in pod["dockerArgs"]

    def test_datacenter_forwarded(self):
        pod = _pod_input(
            self._spec(cpu=["cpu3c-1-2"], datacenter=["EU-RO-1"]), "tok", "t"
        )
        assert pod["dataCenterIds"] == ["EU-RO-1"]


class TestUnwrapTaskResponse:
    def test_success_cloudpickle(self):
        assert unwrap_task_response({"success": True, "result": _b64(42)}) == 42

    def test_success_json(self):
        assert (
            unwrap_task_response({"success": True, "json_result": {"a": 1}})
            == {"a": 1}
        )

    def test_failure_raises(self):
        with pytest.raises(RemoteExecutionError, match="boom"):
            unwrap_task_response({"success": False, "error": "boom"})


class TestPodTargetPayload:
    def test_payload_carries_source_and_args(self):
        app = App("a")

        @app.task(name="t", cpu="cpu3c-1-2", dependencies=["numpy"])
        def t(x):
            return x

        target = PodTarget(t.spec, t._fn)
        payload = target.build_payload(t._fn, t.spec, (5,), {})
        assert payload["function_name"] == "t"
        assert "def t(x):" in payload["function_code"]
        assert _unb64(payload["args"][0]) == 5
        assert payload["dependencies"] == ["numpy"]

    def test_task_remote_runs_full_lifecycle(self):
        app = App("a")

        @app.task(name="t", cpu="cpu3c-1-2")
        def t(x):
            return x * 2

        with patch("runpod.apps.tasks.TaskExecution") as MockExec:
            instance = MockExec.return_value
            instance.start = AsyncMock()
            instance.wait_ready = AsyncMock()
            instance.execute = AsyncMock(
                return_value={"success": True, "result": _b64(10)}
            )
            instance.terminate = AsyncMock()

            result = t.remote(5)

        assert result == 10
        instance.start.assert_awaited_once()
        instance.wait_ready.assert_awaited_once()
        instance.terminate.assert_awaited_once()

    def test_task_terminates_pod_on_failure(self):
        app = App("a")

        @app.task(name="t", cpu="cpu3c-1-2")
        def t():
            pass

        with patch("runpod.apps.tasks.TaskExecution") as MockExec:
            instance = MockExec.return_value
            instance.start = AsyncMock()
            instance.wait_ready = AsyncMock(
                side_effect=TimeoutError("never ready")
            )
            instance.terminate = AsyncMock()

            with pytest.raises(TimeoutError):
                t.remote()

        instance.terminate.assert_awaited_once()

    def test_task_spawn_returns_job(self):
        app = App("a")

        @app.task(name="t", cpu="cpu3c-1-2")
        def t():
            pass

        with patch("runpod.apps.tasks.TaskExecution") as MockExec:
            instance = MockExec.return_value
            instance.start = AsyncMock()
            instance.wait_ready = AsyncMock()
            instance.submit = AsyncMock()
            instance.pod_id = "pod-1"

            job = t.spawn()

        from runpod.apps.tasks import TaskJob

        assert isinstance(job, TaskJob)
        assert job.pod_id == "pod-1"
        instance.submit.assert_awaited_once()
