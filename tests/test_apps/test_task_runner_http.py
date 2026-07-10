"""integration tests for the task runner http server.

boots the real ThreadingHTTPServer on a random port and exercises the
protocol over actual sockets. engine behavior (dependency install,
serialization, stdout capture) is covered in test_executor.py.
"""

import base64
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import cloudpickle
import pytest

from runpod.runtimes.task import runner as task_runner
from runpod.runtimes.task.runner import Handler

# http.server's per-request sockets are collected lazily; the unraisable
# checker flags them as ResourceWarnings non-deterministically
pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning"
)

TOKEN = "test-token"


@pytest.fixture()
def server(monkeypatch):
    monkeypatch.setattr(task_runner, "TOKEN", TOKEN)
    monkeypatch.setattr(
        task_runner, "_job_state", {"status": "NONE", "response": None}
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    thread.join(timeout=5)
    httpd.server_close()


def _request(url, method="GET", body=None, token=TOKEN):
    req = urllib.request.Request(url, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=10) as resp:
        return resp.status, json.loads(resp.read())


def _b64(value):
    return base64.b64encode(cloudpickle.dumps(value)).decode()


def _unb64(value):
    return cloudpickle.loads(base64.b64decode(value))


def test_ping_unauthenticated(server):
    status, body = _request(f"{server}/ping", token=None)
    assert status == 200
    assert body == {"ready": True}


def test_execute_requires_auth(server):
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _request(f"{server}/execute", method="POST", body={}, token="wrong")
    assert exc_info.value.code == 401


def test_execute_roundtrip(server):
    status, body = _request(
        f"{server}/execute",
        method="POST",
        body={
            "function_name": "mul",
            "function_code": "def mul(a, b):\n    return a * b",
            "args": [_b64(6), _b64(7)],
            "kwargs": {},
        },
    )
    assert status == 200
    assert body["success"] is True
    assert _unb64(body["result"]) == 42


def test_submit_and_result(server):
    status, body = _request(
        f"{server}/submit",
        method="POST",
        body={
            "function_name": "quick",
            "function_code": "def quick():\n    return 'done'",
            "args": [],
            "kwargs": {},
            "serialization_format": "json",
        },
    )
    assert status == 200
    assert body == {"status": "RUNNING"}

    import time

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status, body = _request(f"{server}/result")
        if body["status"] == "DONE":
            break
        time.sleep(0.1)

    assert body["status"] == "DONE"
    assert body["response"]["success"] is True
    assert body["response"]["json_result"] == "done"


def test_unknown_path_404(server):
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _request(f"{server}/nope")
    assert exc_info.value.code == 404


def test_single_file_source_is_self_contained():
    """the shipped runner must run without the runpod package installed."""
    import subprocess
    import sys

    from runpod.apps.tasks import _runner_source

    source = _runner_source()
    assert "from runpod.runtimes.executor import" in source
    # compile in an empty namespace with runpod imports failing
    probe = (
        "import sys\n"
        "sys.modules['runpod'] = None\n"  # forces the ImportError branch
        "sys.argv = ['runner']\n"
        + source.replace('if __name__ == "__main__":', "if False:")
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


class TestWatchdog:
    def test_running_job_never_killed(self):
        from runpod.runtimes.task.runner import _should_self_terminate

        assert not _should_self_terminate("RUNNING", 0, 10_000, 600)

    def test_abandoned_before_submit(self):
        from runpod.runtimes.task.runner import _should_self_terminate

        assert _should_self_terminate("NONE", 0, 601, 600)

    def test_uncollected_result(self):
        from runpod.runtimes.task.runner import _should_self_terminate

        assert _should_self_terminate("DONE", 0, 601, 600)

    def test_live_client_keeps_pod(self):
        from runpod.runtimes.task.runner import _should_self_terminate

        # polls every ~2s: last contact is always recent
        assert not _should_self_terminate("DONE", 599, 600, 600)

    def test_no_contact_recorded_yet(self):
        from runpod.runtimes.task.runner import _should_self_terminate

        assert not _should_self_terminate("NONE", None, 10_000, 600)

    def test_authed_requests_touch_contact(self, server, monkeypatch):
        monkeypatch.setattr(task_runner, "_last_contact", {"ts": None})
        _request(f"{server}/result")
        assert task_runner._last_contact["ts"] is not None


class TestSelfTermination:
    def test_terminate_self_calls_graphql_then_exits(self, monkeypatch):
        import contextlib
        import io as _io

        calls = []

        @contextlib.contextmanager
        def _response():
            yield _io.BytesIO(b"{}")

        def fake_urlopen(req, timeout=None):
            calls.append((req.full_url, req.data))
            return _response()

        exits = []
        monkeypatch.setenv("RUNPOD_POD_ID", "pod-1")
        monkeypatch.setenv("RUNPOD_API_KEY", "pod-scoped-key")
        monkeypatch.setattr(
            "urllib.request.urlopen", fake_urlopen
        )
        monkeypatch.setattr(
            task_runner.os, "_exit", lambda code: exits.append(code)
        )
        task_runner._terminate_self()

        assert exits == [0]
        url, body = calls[0]
        assert url.endswith("/graphql")
        assert b"podTerminate" in body
        assert b"pod-1" in body

    def test_terminate_self_exits_without_credentials(self, monkeypatch):
        exits = []
        monkeypatch.delenv("RUNPOD_POD_ID", raising=False)
        monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
        monkeypatch.setattr(
            task_runner.os, "_exit", lambda code: exits.append(code)
        )
        task_runner._terminate_self()
        assert exits == [0]

    def test_terminate_self_survives_api_failure(self, monkeypatch):
        def fail(req, timeout=None):
            raise OSError("network down")

        exits = []
        monkeypatch.setenv("RUNPOD_POD_ID", "pod-1")
        monkeypatch.setenv("RUNPOD_API_KEY", "k")
        monkeypatch.setattr("urllib.request.urlopen", fail)
        monkeypatch.setattr(
            task_runner.os, "_exit", lambda code: exits.append(code)
        )
        task_runner._terminate_self()
        assert exits == [0]
