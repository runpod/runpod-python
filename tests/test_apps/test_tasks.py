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
        from runpod.apps.images import local_python_version

        assert (
            pod["imageName"]
            == f"runpod/task:py{local_python_version()}-latest"
        )
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
        # gpu runtime image matched to the local python version
        from runpod.apps.images import local_python_version

        assert (
            pod["imageName"]
            == f"runpod/task-gpu:py{local_python_version()}-latest"
        )

    def test_custom_image_bootstraps_runner(self):
        pod = _pod_input(
            self._spec(cpu=["cpu3c-1-2"], image="my/image:1", volume="vol-1"),
            "tok",
            "t",
        )
        assert pod["imageName"] == "my/image:1"
        # volumes resolve at TaskExecution.start (placement solve),
        # not in the static pod input
        assert "networkVolumeId" not in pod
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


class TestPollResult:
    """poll_result must tolerate proxy propagation 404s."""

    def _server(self, responses):
        """a local server that pops one canned (status, body) per hit."""
        import http.server
        import threading

        hits = {"count": 0}

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits["count"] += 1
                status, body = responses[min(hits["count"], len(responses)) - 1]
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, hits

    def _poll(self, url):
        import asyncio

        from runpod.apps.tasks import TaskExecution

        spec = ResourceSpec(kind=ResourceKind.TASK, name="t", cpu=["cpu3c-1-2"])
        execution = TaskExecution(spec)
        execution.pod_id = "fake"
        with (
            patch("runpod.apps.tasks._proxy_url", return_value=url),
            patch("runpod.apps.tasks.asyncio.sleep", AsyncMock()),
        ):
            return asyncio.run(execution.poll_result())

    def test_retries_transient_404_then_returns_result(self):
        httpd, hits = self._server(
            [
                (404, {"error": "not found"}),
                (404, {"error": "not found"}),
                (200, {"status": "DONE", "response": {"success": True}}),
            ]
        )
        try:
            url = f"http://127.0.0.1:{httpd.server_port}"
            assert self._poll(url) == {"success": True}
            assert hits["count"] == 3
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_persistent_404_raises(self):
        import aiohttp

        httpd, hits = self._server([(404, {"error": "not found"})])
        try:
            url = f"http://127.0.0.1:{httpd.server_port}"
            with pytest.raises(aiohttp.ClientResponseError):
                self._poll(url)
            assert hits["count"] == 6
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_running_returns_none(self):
        httpd, _ = self._server([(200, {"status": "RUNNING", "response": None})])
        try:
            url = f"http://127.0.0.1:{httpd.server_port}"
            assert self._poll(url) is None
        finally:
            httpd.shutdown()
            httpd.server_close()


class TestGpuPoolExpansion:
    def test_pool_id_expands_to_device_names(self):
        from runpod.apps.tasks import _device_names

        assert _device_names(["ADA_24"]) == ["NVIDIA GeForce RTX 4090"]

    def test_device_name_passes_through(self):
        from runpod.apps.tasks import _device_names

        assert _device_names(["NVIDIA L40S"]) == ["NVIDIA L40S"]

    def test_none_expands_to_all_devices(self):
        # the pod api has no "any" wildcard; unconstrained gpu means
        # every known device name
        from runpod.apps.gpu import POOLS_TO_TYPES
        from runpod.apps.tasks import _device_names

        names = _device_names(None)
        assert "any" not in names
        all_devices = {
            t.value for types in POOLS_TO_TYPES.values() for t in types
        }
        assert set(names) == all_devices

    def test_mixed_pool_and_device(self):
        from runpod.apps.tasks import _device_names

        names = _device_names(["AMPERE_48", "NVIDIA L40S"])
        assert "NVIDIA A40" in names and "NVIDIA L40S" in names
