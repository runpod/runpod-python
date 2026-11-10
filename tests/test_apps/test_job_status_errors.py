"""job-status diagnostics and propagation retries after admission."""

import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock, patch

import pytest

from runpod.apps import AppError, JobStatusError
from runpod.apps.targets import JOB_PROPAGATION_ATTEMPTS, QueueClient


@pytest.fixture
def admitted_job_server(monkeypatch):
    state = {"requests": [], "statuses": []}

    class Handler(BaseHTTPRequestHandler):
        def _respond(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            state["requests"].append((self.command, self.path))
            if self.command == "POST":
                status = 200
                data = {"id": "admitted-job", "status": "IN_QUEUE"}
            else:
                status = state["statuses"].pop(0) if state["statuses"] else 200
                data = (
                    {"id": "admitted-job", "status": "COMPLETED", "output": 42}
                    if status == 200
                    else {"error": "Bearer synthetic-api-key synthetic-task-token"}
                )
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _respond

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr("runpod.endpoint_url_base", base_url)
    try:
        yield state, base_url
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("endpoint", ["flash", "direct-endpoint"])
@pytest.mark.parametrize("operation", ["invoke", "status"])
@pytest.mark.parametrize("not_found_count", [1, 6, 7])
async def test_admitted_job_status_propagation(
    admitted_job_server, endpoint, operation, not_found_count
):
    state, base_url = admitted_job_server
    state["statuses"] = [404] * not_found_count
    client = QueueClient(
        endpoint,
        lambda: {
            "Authorization": "Bearer synthetic-api-key",
            "X-Runpod-Task-Token": "synthetic-task-token",
        },
        app_name="demo",
        resource_name="work",
    )
    with patch("runpod.apps.targets.asyncio.sleep", AsyncMock()) as sleep:
        if operation == "invoke":
            call = client.invoke({"input": {}}, timeout=60)
        else:
            job = await client.run({"input": {}})
            call = client.status(job["id"])

        if not_found_count < JOB_PROPAGATION_ATTEMPTS:
            assert (await call)["output"] == 42
        else:
            with pytest.raises(JobStatusError) as raised:
                await call
            error = raised.value
            assert isinstance(error, AppError)
            assert error.job_id == "admitted-job"
            assert error.status == 404
            assert error.method == "GET"
            assert error.url == f"{base_url}/{endpoint}/status/admitted-job"
            diagnostic = (
                str(error)
                + repr(error)
                + "".join(
                    traceback.format_exception(type(error), error, error.__traceback__)
                )
            )
            for value in ("admitted-job", "404", "GET", error.url):
                assert value in diagnostic
            for value in ("deploy", "synthetic-api-key", "synthetic-task-token"):
                assert value not in diagnostic
            assert error.__cause__ is None
            assert error.__context__ is None

    expected_gets = min(not_found_count + 1, JOB_PROPAGATION_ATTEMPTS)
    assert len(state["requests"]) == 1 + expected_gets
    assert state["requests"][0][0] == "POST"
    assert (
        state["requests"][1:]
        == [("GET", f"/{endpoint}/status/admitted-job")] * expected_gets
    )
    delays = [call.args[0] for call in sleep.await_args_list]
    if operation == "invoke":
        assert delays.pop(0) == 0.5
    assert delays == [0.5 * 2**i for i in range(expected_gets - 1)]


@pytest.mark.parametrize("status,attempts", [(400, 1), (401, 1), (503, 7)])
async def test_job_status_http_failures(admitted_job_server, status, attempts):
    state, _ = admitted_job_server
    state["statuses"] = [status] * JOB_PROPAGATION_ATTEMPTS
    client = QueueClient("endpoint", lambda: {})
    job = await client.run({"input": {}})
    with patch("runpod.apps.targets.asyncio.sleep", AsyncMock()):
        with pytest.raises(JobStatusError) as raised:
            await client.status(job["id"])
    assert raised.value.status == status
    assert raised.value.job_id == job["id"]
    assert len(state["requests"]) == 1 + attempts


def test_job_status_error_sanitizes_request_url():
    error = JobStatusError(
        "admitted-job",
        404,
        "GET",
        "https://synthetic-user:synthetic-password@api.runpod.ai/v2/flash/"
        "status/admitted-job?token=synthetic-query#synthetic-fragment",
    )
    assert error.url == "https://api.runpod.ai/v2/flash/status/admitted-job"
    assert "synthetic" not in str(error) + repr(error) + repr(vars(error))
