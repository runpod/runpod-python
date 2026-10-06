"""Sandbox lifecycle and streaming regressions over an actual HTTP connection."""

import asyncio
import subprocess
import sys
import threading
from collections import deque
from concurrent.futures import Future
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from aiohttp import web

from runpod import AsyncioSandbox, Sandbox
from runpod.apps.volume import GlobalVolume, NetworkVolume, VolumeError
from runpod.error import AuthenticationError, QueryError
from runpod.sandbox import (
    SandboxCreationError,
    SandboxExecutionError,
    SandboxStartupTimeout,
    SandboxStateError,
)


class SandboxService:
    """A controllable REST peer for lifecycle races, not a mocked SDK transport."""

    def __init__(self):
        self.records = {}
        self.requests = []
        self.executed = []
        self.exec_statuses = deque()
        self.exec_responses = deque()
        self.log_statuses = deque()
        self.conflict_code = "sandbox_starting"
        self.network_volumes = []
        self.global_volumes = []
        self.global_error = None
        self.registries = []
        self.delete_status = 204
        self.create_status = 201
        self.create_error = "container provisioning failed"
        self.job_started = threading.Event()
        self.job_completed = threading.Event()
        self.allow_job = threading.Event()
        self.jobs = set()
        self.create_started = threading.Event()
        self.allow_create = threading.Event()
        self.allow_create.set()
        self.log_started = threading.Event()
        self.allow_logs = threading.Event()
        self.allow_logs.set()
        self.log_disconnected = threading.Event()
        self.hold_logs = False
        self.stopping = threading.Event()
        # Mix line endings, split a UTF-8 codepoint, preserve an opaque SSE id,
        # and include both a multiline data event and a non-JSON timeout frame.
        self.log_bytes = (
            ": heartbeat\r\n\r\nid: opaque/42\r\nevent: message\r\n"
            'data: {"source":"container",\r\n'
            'data: "line":"caf\u00e9","ts":"2026-09-14T12:00:00Z"}\r\n\r\n'
            'id: opaque/43\rdata: {"source":"system","line":"started",'
            '"ts":"2026-09-14T12:00:01Z"}\r\r'
            "event: timeout\ndata: max stream duration reached\n\n"
        ).encode()

    def create_record(self, body):
        sandbox_id = f"sandbox-{len(self.records) + 1}"
        now = "2026-09-14T12:00:00Z"
        record = {
            "id": sandbox_id,
            "name": body.get("name", sandbox_id),
            "state": "RUNNING",
            "imageName": body.get("imageName"),
            "templateId": body.get("templateId"),
            "idleTimeoutSeconds": body.get("idleTimeoutSeconds", 300),
            "maxLifetimeSeconds": body.get("maxLifetimeSeconds", 900),
            "lastActivityAt": now,
            "idleExpiresAt": "2026-09-14T12:05:00Z",
            "expiresAt": "2026-09-14T12:15:00Z",
            "createdAt": now,
            "updatedAt": now,
            "labels": body.get("labels", {}),
            "cpuFlavorId": body.get("cpuFlavorId"),
            "dataCenterId": (body.get("dataCenterIds") or [None])[0],
            "env": body.get("env", {}),
            "registry": body.get("registry"),
            "startedAt": now,
            "ssh": {"proxy": {"command": "ssh sandbox@ssh.runpod.io"}},
            "ports": body.get("ports", []),
            "mounts": body.get("mounts", {"network": [], "global": []}),
            "cmd": body.get("cmd", ["tail", "-f", "/dev/null"]),
            "entrypoint": body.get("entrypoint", []),
            "compute": {
                "vcpuCount": body.get("vcpuCount", 2),
                "memoryInGb": body.get("memoryInGb", 4),
                "containerDiskInGb": body.get("disk", 10),
                "costPerHr": 0.026,
            },
        }
        self.records[sandbox_id] = record
        return record


@contextmanager
def sandbox_peer():
    service = SandboxService()
    ready, stopped = threading.Event(), asyncio.Event()

    def failure(status, detail, code=None):
        payload = {"detail": detail}
        if code is not None:
            payload["code"] = code
        return web.json_response(
            payload, status=status, content_type="application/problem+json"
        )

    async def handle(request):
        body = await request.json() if request.can_read_body else None
        service.requests.append(
            (request.method, request.path, body, dict(request.headers))
        )
        if request.headers.get("Authorization") != "Bearer sandbox-test-key":
            return failure(401, "invalid key")
        if request.path == "/graphql":
            if service.global_error:
                return web.json_response(
                    {"errors": [{"message": service.global_error}]}
                )
            query = body["query"]
            if "globalStoreBucketCreate(" in query:
                volume = {
                    "id": f"global-{len(service.global_volumes) + 1}",
                    "name": body["variables"]["input"]["name"],
                }
                service.global_volumes.append(volume)
                return web.json_response({"data": {"globalStoreBucketCreate": volume}})
            if "globalStoreBuckets" in query:
                return web.json_response(
                    {"data": {"myself": {"globalStoreBuckets": service.global_volumes}}}
                )
            return failure(400, "unknown graphql operation")
        if request.path == "/v2/network-volumes":
            if request.method == "POST":
                volume = {"id": "created-volume", **body}
                service.network_volumes.append(volume)
                return web.json_response(volume, status=201)
            return web.json_response({"networkVolumes": service.network_volumes})
        if request.path == "/v2/registries":
            return web.json_response({"registries": service.registries})
        sandbox_id = request.match_info.get("id")
        if sandbox_id is None:
            if request.method == "POST":
                record = service.create_record(body)
                service.create_started.set()
                await asyncio.to_thread(service.allow_create.wait, 5)
                if service.create_status != 201:
                    return web.json_response(
                        {"detail": service.create_error, "sandboxId": record["id"]},
                        status=service.create_status,
                        content_type="application/problem+json",
                    )
                return web.json_response(record, status=201)
            state = request.query.get("state")
            labels = [term.split("=", 1) for term in request.query.getall("labels", [])]
            records = [
                record
                for record in service.records.values()
                if (
                    record["state"] == state
                    if state
                    else record["state"] != "TERMINATED"
                )
                and all(record["labels"].get(key) == value for key, value in labels)
            ]
            return web.json_response({"sandboxes": records})
        if sandbox_id not in service.records:
            return failure(404, "missing sandbox")
        record = service.records[sandbox_id]
        operation = request.match_info.get("operation")
        if request.method == "PATCH" or operation == "extend":
            if (
                operation == "extend"
                and body["maxLifetimeSeconds"] < record["maxLifetimeSeconds"]
            ):
                return failure(422, "extension cannot reduce lifetime")
            for field, origin, deadline in (
                ("idleTimeoutSeconds", "lastActivityAt", "idleExpiresAt"),
                ("maxLifetimeSeconds", "createdAt", "expiresAt"),
            ):
                if field in body:
                    record[field] = body[field]
                    start = datetime.fromisoformat(
                        record[origin].replace("Z", "+00:00")
                    )
                    record[deadline] = (
                        start + timedelta(seconds=body[field])
                    ).isoformat()
            return web.json_response(record)
        if request.method == "DELETE":
            if service.delete_status != 204:
                return failure(service.delete_status, "cleanup unavailable")
            record.update(state="TERMINATED", compute=None)
            return web.Response(status=204)
        if operation == "exec":
            timeout = body.get("timeoutSeconds", 50)
            background = body.get("background", False)
            if (
                type(timeout) is not int
                or not 1 <= timeout <= 50
                or type(background) is not bool
            ):
                return failure(422, "invalid execution controls")
            status = service.exec_statuses.popleft() if service.exec_statuses else 200
            if status == 409 or record["state"] in ("FAILED", "TERMINATED"):
                return failure(409, "container not started", service.conflict_code)
            service.executed.append(body["command"])
            if status >= 400:
                return failure(status, "response lost after command execution")
            if service.exec_responses:
                return service.exec_responses.popleft()
            if body["command"] == ["wait-for-release"]:
                async def run_job():
                    service.job_started.set()
                    while not service.allow_job.is_set():
                        if service.stopping.is_set():
                            return
                        await asyncio.sleep(0.01)
                    service.job_completed.set()

                if background:
                    task = asyncio.create_task(run_job())
                    service.jobs.add(task)
                    task.add_done_callback(service.jobs.discard)
                    return web.json_response({"output": "started", "exitCode": None})
                try:
                    await asyncio.wait_for(run_job(), timeout)
                except asyncio.TimeoutError:
                    return web.json_response(
                        {"output": "started", "error": "execution timed out"}
                    )
                return web.json_response({"output": "finished", "exitCode": 0})
            if body["command"] == ["job-status"]:
                return web.json_response(
                    {
                        "output": (
                            "completed" if service.job_completed.is_set() else "pending"
                        ),
                        "exitCode": 0,
                    }
                )
            result = (
                {"output": "partial output", "error": "command failed"}
                if body["command"] == ["fail"]
                else {"output": "completed"}
            )
            return web.json_response(result)
        if operation == "logs":
            service.log_started.set()
            await asyncio.to_thread(service.allow_logs.wait, 5)
            status = service.log_statuses.popleft() if service.log_statuses else 200
            if status != 200:
                return failure(status, "logs not ready", service.conflict_code)
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            try:
                await response.prepare(request)
                for byte in service.log_bytes:
                    await response.write(bytes([byte]))
                    await asyncio.sleep(0)
                while service.hold_logs and not service.stopping.is_set():
                    await response.write(b": heartbeat\n\n")
                    await asyncio.sleep(0.01)
            except ConnectionResetError:
                # clients may stop reading before the fixture finishes streaming.
                pass
            finally:
                service.log_disconnected.set()
            return response
        return web.json_response(record)

    async def serve():
        service.loop = asyncio.get_running_loop()
        app = web.Application()
        for path in (
            "/graphql",
            "/v2/sandboxes",
            "/v2/network-volumes",
            "/v2/registries",
            "/v2/sandboxes/{id}",
            "/v2/sandboxes/{id}/{operation}",
        ):
            app.router.add_route("*", path, handle)
        runner = web.AppRunner(app, access_log=None, shutdown_timeout=1)
        await runner.setup()
        try:
            await web.TCPSite(runner, "127.0.0.1", 0).start()
            service.options = {
                "api_key": "sandbox-test-key",
                "base_url": f"http://127.0.0.1:{runner.addresses[0][1]}",
                "request_timeout": 2,
                "startup_timeout": 2,
            }
            ready.set()
            await stopped.wait()
        finally:
            await runner.cleanup()
            if service.jobs:
                await asyncio.gather(*service.jobs)

    outcome = Future()

    def run_peer():
        try:
            asyncio.run(serve())
        except BaseException as error:
            outcome.set_exception(error)
        else:
            outcome.set_result(None)

    thread = threading.Thread(target=run_peer, daemon=True)
    thread.start()
    assert ready.wait(5), "local sandbox peer did not start"
    try:
        yield service
    finally:
        service.allow_create.set()
        service.allow_logs.set()
        service.allow_job.set()
        service.stopping.set()
        service.loop.call_soon_threadsafe(stopped.set)
        thread.join(5)
        assert not thread.is_alive(), "local sandbox peer did not stop"
        outcome.result(timeout=0)


@pytest.fixture
def peer():
    with sandbox_peer() as service:
        yield service


@pytest.mark.asyncio
@pytest.mark.parametrize("async_api", [False, True], ids=["sync", "async"])
async def test_execution_controls_bound_foreground_and_detach_background(peer, async_api):
    api = AsyncioSandbox if async_api else Sandbox

    async def invoke(method, *args, **kwargs):
        if async_api:
            return await method(*args, **kwargs)
        return await asyncio.to_thread(method, *args, **kwargs)

    sandbox = await invoke(api.create, image="image", **peer.options)
    try:
        for controls in (
            {"timeout_seconds": 0},
            {"timeout_seconds": 51},
            {"timeout_seconds": True},
            {"timeout_seconds": 1.5},
            {"background": 1},
        ):
            with pytest.raises((TypeError, ValueError)):
                await invoke(sandbox.exec, ["wait-for-release"], **controls)
        assert peer.executed == []
        assert not any(path.endswith("/exec") for _, path, _, _ in peer.requests)

        with pytest.raises(SandboxExecutionError) as expired:
            await invoke(
                sandbox.exec, ["wait-for-release"], timeout_seconds=1, check=True
            )
        assert expired.value.result.output == "started"
        assert expired.value.result.error == "execution timed out"
        assert not peer.job_completed.is_set()

        peer.allow_job.set()
        completed = await invoke(
            sandbox.exec, ["wait-for-release"], timeout_seconds=50, check=True
        )
        assert completed.output == "finished"
        assert completed.exit_code == 0
        assert peer.job_completed.is_set()

        peer.allow_job.clear()
        peer.job_started.clear()
        peer.job_completed.clear()
        launched = await invoke(
            sandbox.exec,
            ["wait-for-release"],
            timeout_seconds=1,
            background=True,
            check=True,
        )
        assert launched.output == "started"
        assert await asyncio.to_thread(peer.job_started.wait, 2)
        assert not peer.job_completed.is_set()
        assert (await invoke(sandbox.exec, ["job-status"])).output == "pending"
        peer.allow_job.set()
        assert await asyncio.to_thread(peer.job_completed.wait, 2)
        assert (await invoke(sandbox.exec, ["job-status"])).output == "completed"
    finally:
        peer.allow_job.set()
        await invoke(sandbox.terminate)


def test_sync_submission_racing_close_settles_and_allows_reuse():
    script = """
import asyncio
import aiohttp
import threading
from concurrent.futures import Future
from runpod import Sandbox
from runpod.error import QueryError
from tests.test_sandbox import sandbox_peer

with sandbox_peer() as peer:
    sandbox = Sandbox.create(image="image", **peer.options)
    loop = sandbox._runner._loop
    schedule = loop.call_soon_threadsafe
    submitting = threading.Event()
    release_submission = threading.Event()
    submitted = threading.Event()
    stopping = threading.Event()
    release_loop = threading.Event()
    refreshed = Future()
    closed = Future()

    def controlled_schedule(callback, *args, **kwargs):
        if threading.current_thread().name == "racing-refresh":
            submitting.set()
            assert release_submission.wait(4), "submission was not released"
            try:
                return schedule(callback, *args, **kwargs)
            finally:
                submitted.set()
        if callback == loop.stop:
            schedule(release_loop.wait, 4)
            result = schedule(callback, *args, **kwargs)
            stopping.set()
            return result
        return schedule(callback, *args, **kwargs)

    def refresh():
        try:
            refreshed.set_result(sandbox.refresh())
        except BaseException as error:
            refreshed.set_exception(error)

    def close():
        try:
            sandbox.close()
            closed.set_result(None)
        except BaseException as error:
            closed.set_exception(error)

    loop.call_soon_threadsafe = controlled_schedule
    reader = threading.Thread(target=refresh, name="racing-refresh", daemon=True)
    closer = threading.Thread(target=close, daemon=True)
    try:
        reader.start()
        assert submitting.wait(2), "refresh never reached submission"
        closer.start()
        # admission may exclude shutdown until the submission gate is released.
        stopping.wait(1)
        release_submission.set()
        assert submitted.wait(2), "refresh submission did not resume"
        release_loop.set()
        try:
            assert refreshed.result(timeout=3).state == "RUNNING"
        except (RuntimeError, QueryError, asyncio.CancelledError, aiohttp.ClientConnectionError):
            pass
        closed.result(timeout=3)
        reader.join(2)
        closer.join(2)
        assert not reader.is_alive(), "refresh was abandoned by shutdown"
        assert not closer.is_alive(), "close did not finish"
        assert sandbox.refresh().state == "RUNNING"
    finally:
        release_submission.set()
        release_loop.set()
        loop.call_soon_threadsafe = schedule
    sandbox.terminate()
    assert peer.records[sandbox.id]["state"] == "TERMINATED"
"""
    process = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=15
    )
    assert process.returncode == 0, process.stdout + process.stderr


@pytest.mark.parametrize("delete_status", [204, 503])
def test_sync_failed_creation_preserves_resource_id_and_cleans_up(peer, delete_status):
    peer.create_status = 502
    peer.delete_status = delete_status
    with pytest.raises(SandboxCreationError) as failed:
        Sandbox.create(image="image", **peer.options)
    error = failed.value
    assert isinstance(error, QueryError)
    assert error.sandbox_id == "sandbox-1"
    assert error.status_code == 502
    assert peer.create_error in str(error)
    assert [method for method, path, _, _ in peer.requests if path == "/v2/sandboxes"] == [
        "POST"
    ]
    assert [
        method
        for method, path, _, _ in peer.requests
        if path == "/v2/sandboxes/sandbox-1"
    ] == ["DELETE"]
    if delete_status == 204:
        assert peer.records[error.sandbox_id]["state"] == "TERMINATED"
    else:
        assert isinstance(error.__cause__, QueryError)
        assert error.__cause__.status_code == 503
        assert peer.records[error.sandbox_id]["state"] == "RUNNING"
        peer.delete_status = 204
        Sandbox.get(error.sandbox_id, **peer.options).terminate()
        assert peer.records[error.sandbox_id]["state"] == "TERMINATED"


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_status", [204, 503])
async def test_async_failed_creation_preserves_resource_id_and_cleans_up(
    peer, delete_status
):
    peer.create_status = 502
    peer.delete_status = delete_status
    with pytest.raises(SandboxCreationError) as failed:
        await AsyncioSandbox.create(image="image", **peer.options)
    error = failed.value
    assert error.sandbox_id == "sandbox-1"
    assert error.status_code == 502
    assert peer.create_error in str(error)
    assert [method for method, path, _, _ in peer.requests if path == "/v2/sandboxes"] == [
        "POST"
    ]
    assert [
        method
        for method, path, _, _ in peer.requests
        if path == "/v2/sandboxes/sandbox-1"
    ] == ["DELETE"]
    if delete_status == 204:
        assert peer.records[error.sandbox_id]["state"] == "TERMINATED"
    else:
        assert isinstance(error.__cause__, QueryError)
        assert error.__cause__.status_code == 503
        peer.delete_status = 204
        recovery = await AsyncioSandbox.get(error.sandbox_id, **peer.options)
        await recovery.terminate()
        assert peer.records[error.sandbox_id]["state"] == "TERMINATED"


@pytest.mark.parametrize(
    "operation, delete_status",
    [
        ("create", 204),
        ("enter", 204),
        ("get", 204),
        ("borrowed_enter", 204),
        ("create", 503),
        ("enter", 503),
    ],
)
def test_sync_interrupt_at_successful_handle_handoff(
    peer, monkeypatch, operation, delete_status
):
    peer.delete_status = delete_status
    borrowed = operation in ("get", "borrowed_enter")
    handle = None
    if borrowed:
        peer.create_record({"imageName": "image"})
    if operation == "borrowed_enter":
        handle = Sandbox.get("sandbox-1", **peer.options)
    elif operation == "enter":
        handle = Sandbox(image="image", **peer.options)
    original_result = Future.result
    caller = threading.get_ident()
    interrupt = KeyboardInterrupt("interrupted after remote success")
    armed = True

    def interrupt_result(future, *args, **kwargs):
        nonlocal armed
        result = original_result(future, *args, **kwargs)
        if armed and threading.get_ident() == caller:
            armed = False
            raise interrupt
        return result

    monkeypatch.setattr(Future, "result", interrupt_result)
    try:
        with pytest.raises(KeyboardInterrupt) as failed:
            if operation == "create":
                Sandbox.create(image="image", **peer.options)
            elif operation == "get":
                Sandbox.get("sandbox-1", **peer.options)
            else:
                handle.__enter__()
        assert failed.value is interrupt
        assert peer.records["sandbox-1"]["state"] == (
            "RUNNING" if borrowed or delete_status != 204 else "TERMINATED"
        )
        deletes = [path for method, path, _, _ in peer.requests if method == "DELETE"]
        assert deletes == ([] if borrowed else ["/v2/sandboxes/sandbox-1"])
        if delete_status != 204:
            assert isinstance(failed.value.__cause__, QueryError)
            assert failed.value.__cause__.status_code == 503
            peer.delete_status = 204
            Sandbox.get("sandbox-1", **peer.options).terminate()
            assert peer.records["sandbox-1"]["state"] == "TERMINATED"
        if operation == "borrowed_enter":
            with handle:
                assert handle.refresh().state == "RUNNING"
    finally:
        armed = False
        if handle is not None:
            handle.close()


def test_overlapping_sync_entries_preserve_the_first_owner(peer):
    sandbox = Sandbox(image="image", **peer.options)
    peer.allow_create.clear()
    leave = threading.Event()
    entered = threading.Event()
    outcome = Future()

    def own_context():
        try:
            with sandbox:
                entered.set()
                assert leave.wait(4), "test did not release the first context"
                assert sandbox.refresh().state == "RUNNING"
            outcome.set_result(None)
        except BaseException as error:
            outcome.set_exception(error)

    worker = threading.Thread(target=own_context, daemon=True)
    worker.start()
    try:
        assert peer.create_started.wait(2)
        with pytest.raises(RuntimeError):
            sandbox.__enter__()
        peer.allow_create.set()
        assert entered.wait(3), "first context was disrupted by a rejected entry"
    finally:
        peer.allow_create.set()
        leave.set()
        worker.join(5)
        assert not worker.is_alive(), "first context did not finish"
        try:
            outcome.result(timeout=0)
        finally:
            sandbox.close()
    assert peer.records["sandbox-1"]["state"] == "TERMINATED"
    assert [method for method, _, _, _ in peer.requests].count("POST") == 1


def test_sync_startup_conflict_and_borrowed_context_ownership(peer):
    peer.exec_statuses.extend([409, 200])
    with Sandbox(image="python:3.12-slim", **peer.options) as owner:
        with Sandbox.get(owner.id, **peer.options) as borrowed:
            assert borrowed.exec(["work"]).output == "completed"
        assert peer.records[owner.id]["state"] == "RUNNING"
        # A rejected nested entry must not destroy the outer context's client.
        with pytest.raises(RuntimeError):
            with owner:
                pytest.fail("nested context entry succeeded")
        assert owner.refresh().state == "RUNNING"
    assert peer.records[owner.id]["state"] == "TERMINATED"
    assert peer.executed == [["work"]]


@pytest.mark.asyncio
async def test_async_startup_conflict_and_borrowed_context_ownership(peer):
    peer.exec_statuses.extend([409, 200])
    async with AsyncioSandbox(image="python:3.12-slim", **peer.options) as owner:
        async with await AsyncioSandbox.get(owner.id, **peer.options) as borrowed:
            assert (await borrowed.exec(["work"], check=True)).output == "completed"
        assert peer.records[owner.id]["state"] == "RUNNING"
        with pytest.raises(RuntimeError):
            async with owner:
                pytest.fail("nested context entry succeeded")
        assert (await owner.refresh()).state == "RUNNING"
    assert peer.records[owner.id]["state"] == "TERMINATED"
    assert peer.executed == [["work"]]


@pytest.mark.asyncio
async def test_cancellation_during_creation_recovers_id_and_terminates(peer):
    peer.allow_create.clear()

    async def create():
        async with AsyncioSandbox(image="python:3.12-slim", **peer.options):
            pytest.fail("cancelled context was entered")

    task = asyncio.create_task(create())
    assert await asyncio.to_thread(peer.create_started.wait, 2)
    task.cancel()
    peer.allow_create.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 4)
    assert peer.records["sandbox-1"]["state"] == "TERMINATED"


def test_sync_interrupt_during_creation_waits_for_resource_cleanup():
    script = """
import os
import signal
import threading
from runpod import Sandbox
from tests.test_sandbox import sandbox_peer

with sandbox_peer() as peer:
    peer.allow_create.clear()
    def interrupt():
        assert peer.create_started.wait(2)
        os.kill(os.getpid(), signal.SIGINT)
        threading.Timer(0.1, peer.allow_create.set).start()
    worker = threading.Thread(target=interrupt)
    worker.start()
    try:
        Sandbox.create(image="python:3.12-slim", **peer.options)
        raise AssertionError("SIGINT was swallowed")
    except KeyboardInterrupt:
        assert peer.records["sandbox-1"]["state"] == "TERMINATED"
    finally:
        worker.join()
"""
    process = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=10
    )
    assert process.returncode == 0, process.stdout + process.stderr


@pytest.mark.parametrize("delete_status", [204, 503])
@pytest.mark.parametrize("failure_type", [ValueError, KeyboardInterrupt, SystemExit])
def test_sync_body_exception_remains_primary_during_cleanup(
    peer, delete_status, failure_type
):
    peer.delete_status = delete_status
    failure = failure_type("application failed")
    caught = None
    try:
        with Sandbox(image="python:3.12-slim", **peer.options):
            raise failure
    except failure_type as error:
        caught = error
    assert caught is failure
    if delete_status == 204:
        assert peer.records["sandbox-1"]["state"] == "TERMINATED"
    else:
        assert isinstance(caught.__cause__, QueryError)
        assert caught.__cause__.status_code == 503
        assert peer.records["sandbox-1"]["state"] == "RUNNING"


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_status", [204, 503])
async def test_async_body_exception_remains_primary_during_cleanup(peer, delete_status):
    peer.delete_status = delete_status
    failure = ValueError("application failed")
    caught = None
    try:
        async with AsyncioSandbox(image="python:3.12-slim", **peer.options):
            raise failure
    except ValueError as error:
        caught = error
    assert caught is failure
    if delete_status == 204:
        assert peer.records["sandbox-1"]["state"] == "TERMINATED"
    else:
        assert isinstance(caught.__cause__, QueryError)
        assert caught.__cause__.status_code == 503


@pytest.mark.asyncio
async def test_exec_never_replays_ambiguous_failure_and_preserves_partial_output(peer):
    async with AsyncioSandbox(image="python:3.12-slim", **peer.options) as sandbox:
        peer.exec_statuses.append(500)
        with pytest.raises(QueryError) as raised:
            await sandbox.exec(["side-effect"])
        assert raised.value.status_code == 500
        assert peer.executed == [["side-effect"]]
        result = await sandbox.exec(["fail"])
        assert result.output == "partial output"
        assert result.error == "command failed"
        with pytest.raises(SandboxExecutionError) as failure:
            await sandbox.exec(["fail"], check=True)
        assert failure.value.result == result
        assert failure.value.sandbox_id == sandbox.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body, content_type",
    [
        ("{}", "application/json"),
        ('{"output": null}', "application/json"),
        ("<html>proxy error</html>", "text/html"),
        ("[]", "application/json"),
    ],
)
async def test_malformed_exec_response_is_a_query_error_without_replay(
    peer, body, content_type
):
    async with AsyncioSandbox(image="python:3.12-slim", **peer.options) as sandbox:
        peer.exec_responses.append(web.Response(text=body, content_type=content_type))
        with pytest.raises(QueryError) as failure:
            await sandbox.exec(["side-effect"])
        assert failure.value.query == f"POST /v2/sandboxes/{sandbox.id}/exec"
        assert peer.executed == [["side-effect"]]


@pytest.mark.asyncio
async def test_startup_deadline_and_terminal_state_do_not_execute_commands(peer):
    async with AsyncioSandbox(image="python:3.12-slim", **peer.options) as sandbox:
        peer.exec_statuses.extend([409] * 20)
        with pytest.raises(QueryError) as rejected:
            await sandbox.exec(["work"], startup_timeout=0)
        assert rejected.value.status_code == 409
        with pytest.raises(SandboxStartupTimeout) as expired:
            await sandbox.exec(["work"], startup_timeout=0.03)
        assert expired.value.sandbox_id == sandbox.id
        peer.records[sandbox.id]["state"] = "FAILED"
        with pytest.raises(SandboxStateError) as failed:
            await sandbox.exec(["work"])
        assert failed.value.info.state == "FAILED"
        assert peer.executed == []


def test_sync_sse_decodes_fragments_and_retains_resume_cursor(peer):
    with Sandbox(image="python:3.12-slim", **peer.options) as sandbox:
        with sandbox.logs(source="container", tail=0) as logs:
            events = list(logs)
        assert [(event.source, event.line, event.id) for event in events] == [
            ("container", "caf\u00e9", "opaque/42"),
            ("system", "started", "opaque/43"),
        ]
        assert events[0].timestamp.isoformat() == "2026-09-14T12:00:00+00:00"
        with sandbox.logs(last_event_id=events[-1].id) as logs:
            assert next(logs).line == "caf\u00e9"
        log_requests = [
            request for request in peer.requests if request[1].endswith("/logs")
        ]
        assert log_requests[-1][3]["Last-Event-ID"] == "opaque/43"


@pytest.mark.asyncio
async def test_async_log_early_close_releases_live_connection(peer):
    peer.hold_logs = True
    # Exclude the timeout frame so this stays open until the client closes it.
    peer.log_bytes = peer.log_bytes.split(b"event: timeout")[0]
    peer.log_statuses.extend([409, 200])
    async with AsyncioSandbox(image="python:3.12-slim", **peer.options) as sandbox:
        async with sandbox.logs() as logs:
            event = await logs.__anext__()
            assert event.line == "caf\u00e9"
        assert await asyncio.to_thread(peer.log_disconnected.wait, 2)


@pytest.mark.asyncio
async def test_closing_sandbox_cancels_pending_log_handshake(peer):
    peer.allow_logs.clear()
    sandbox = await AsyncioSandbox.create(image="python:3.12-slim", **peer.options)
    logs = sandbox.logs()
    opening = asyncio.create_task(logs.open())
    try:
        assert await asyncio.to_thread(peer.log_started.wait, 2)
        await asyncio.wait_for(sandbox.close(), 3)
        await asyncio.gather(opening, return_exceptions=True)
        assert opening.cancelled()
        assert peer.records[sandbox.id]["state"] == "RUNNING"
    finally:
        peer.allow_logs.set()
        await sandbox.terminate()


@pytest.mark.asyncio
async def test_list_filters_and_independent_handles_do_not_delete_resources(peer):
    first = peer.create_record(
        {"imageName": "image", "labels": {"team": "a", "job": "42"}}
    )
    second = peer.create_record(
        {"imageName": "image", "labels": {"team": "a", "job": "43"}}
    )
    peer.create_record({"imageName": "image", "labels": {"team": "b", "job": "42"}})
    matches = await AsyncioSandbox.list(
        labels={"team": "a", "job": "42"}, **peer.options
    )
    assert [sandbox.id for sandbox in matches] == [first["id"]]
    await matches[0].close()
    handles = await AsyncioSandbox.list(labels={"team": "a"}, **peer.options)
    await handles[0].close()
    async with handles[1] as borrowed:
        assert (await borrowed.exec(["work"])).output == "completed"
        assert borrowed.id == second["id"]
    assert all(record["state"] == "RUNNING" for record in peer.records.values())


@pytest.mark.asyncio
async def test_list_authentication_failure_propagates_after_cleanup(peer):
    options = {**peer.options, "api_key": "invalid-key"}
    with pytest.raises(AuthenticationError):
        await AsyncioSandbox.list(**options)


def test_mixed_mounts_are_lazy_remote_bindings_with_explicit_transport(peer):
    network = NetworkVolume("datasets", create=False)
    global_volume = GlobalVolume("global-models")
    peer.network_volumes = [
        {"id": "network-data", "name": "datasets", "dataCenter": "US-KS-2"}
    ]
    peer.global_volumes = [{"id": "gv-models", "name": "global-models"}]
    peer.registries = [{"id": "registry-id", "name": "private-images"}]
    sandbox = Sandbox(
        image="private/image",
        mounts={"/datasets/": network, "/models": global_volume},
        data_center_ids=["US-KS-2", "US-TX-3"],
        registry_auth="private-images",
        ports={8080: "http", 53: ("tcp", "udp")},
        disk_gb=20,
        cmd=["python", "-m", "http.server"],
        entrypoint=[],
        start_ssh=True,
        env={},
        **peer.options,
    )
    assert peer.requests == []
    with sandbox:
        with pytest.raises(VolumeError):
            _ = network.path
        with pytest.raises(VolumeError):
            _ = global_volume.path
        assert sandbox.info.mounts == {
            "network": [{"volumeId": "network-data", "path": "/datasets"}],
            "global": [{"volumeId": "gv-models", "path": "/models"}],
        }
        assert sandbox.info.data_center_id == "US-KS-2"
        assert sandbox.info.registry == "registry-id"
        assert sandbox.info.compute.container_disk_in_gb == 20
        assert sandbox.info.cmd == ["python", "-m", "http.server"]
        assert sandbox.info.entrypoint == []
        assert sandbox.info.ports == [
            {"port": 8080, "protocol": "http"},
            {"port": 53, "protocol": "tcp"},
            {"port": 53, "protocol": "udp"},
        ]
        create_body = next(
            body
            for method, path, body, _ in peer.requests
            if path == "/v2/sandboxes" and method == "POST"
        )
        assert create_body["dataCenterIds"] == ["US-KS-2"]
        assert create_body["startSsh"] is True
        assert "RUNPOD_MOUNTS" not in create_body["env"]
    assert all(
        headers["Authorization"] == "Bearer sandbox-test-key"
        for _, _, _, headers in peer.requests
    )
    assert not any(path.startswith("/v2/catalog") for _, path, _, _ in peer.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("data_center_ids", [None, ["US-TX-3", "US-KS-2"]])
async def test_global_mount_does_not_lookup_or_pin_network_placement(
    peer, data_center_ids
):
    peer.global_volumes = [{"id": "global-models", "name": "shared-models"}]
    async with AsyncioSandbox(
        image="image",
        mounts={"/models": GlobalVolume("global-models")},
        data_center_ids=data_center_ids,
        **peer.options,
    ) as sandbox:
        assert sandbox.info.mounts == {
            "global": [{"volumeId": "global-models", "path": "/models"}]
        }
        create_body = next(
            body
            for method, path, body, _ in peer.requests
            if path == "/v2/sandboxes" and method == "POST"
        )
        if data_center_ids is None:
            assert "dataCenterIds" not in create_body
        else:
            assert create_body["dataCenterIds"] == data_center_ids
        assert not any(
            path.startswith(("/v2/catalog", "/v2/network-volumes"))
            for _, path, _, _ in peer.requests
        )


@pytest.mark.asyncio
async def test_global_creation_reuses_storage_by_name_and_id(peer):
    volume = GlobalVolume("shared-models")
    sandbox = AsyncioSandbox(image="image", mounts={"/models": volume}, **peer.options)
    assert peer.requests == []
    async with sandbox:
        assert sandbox.info.mounts == {
            "global": [{"volumeId": "global-1", "path": "/models"}]
        }
    for reference in ("shared-models", "global-1"):
        async with AsyncioSandbox(
            image="image",
            mounts={"/data": GlobalVolume(reference, create=False)},
            **peer.options,
        ) as reused:
            assert reused.info.mounts == {
                "global": [{"volumeId": "global-1", "path": "/data"}]
            }
    assert peer.global_volumes == [{"id": "global-1", "name": "shared-models"}]


@pytest.mark.asyncio
async def test_missing_global_with_creation_disabled_prevents_provisioning(peer):
    with pytest.raises(VolumeError):
        await AsyncioSandbox.create(
            image="image",
            mounts={"/models": GlobalVolume("missing-models", create=False)},
            **peer.options,
        )
    assert peer.global_volumes == []
    assert peer.records == {}


@pytest.mark.asyncio
async def test_graphql_global_lookup_failure_prevents_provisioning(peer):
    peer.global_error = "global store access denied"
    with pytest.raises(QueryError):
        await AsyncioSandbox.create(
            image="image",
            mounts={"/models": GlobalVolume("shared-models")},
            **peer.options,
        )
    assert peer.global_volumes == []
    assert peer.records == {}


@pytest.mark.asyncio
async def test_omitted_and_empty_mounts_preserve_template_override_semantics(peer):
    async with AsyncioSandbox(template_id="template", **peer.options):
        assert "mounts" not in peer.requests[0][2]
    async with AsyncioSandbox(
        template_id="template", mounts={}, ports={}, cmd=[], **peer.options
    ):
        body = next(
            body
            for method, path, body, _ in reversed(peer.requests)
            if method == "POST"
        )
        assert body["mounts"] == {}
        assert body["ports"] == []
        assert body["cmd"] == []
        assert "entrypoint" not in body


@pytest.mark.asyncio
async def test_network_creation_requires_explicit_placement_and_checks_requested_dc(
    peer,
):
    missing = NetworkVolume("new-dataset")
    with pytest.raises(VolumeError):
        await AsyncioSandbox.create(
            image="image", mounts={"/data": missing}, **peer.options
        )
    assert peer.records == {}
    assert peer.network_volumes == []
    volume = NetworkVolume("new-dataset", size=25, datacenter="US-KS-2")
    async with AsyncioSandbox(
        image="image", mounts={"/data": volume}, **peer.options
    ) as sandbox:
        assert sandbox.info.data_center_id == "US-KS-2"
        assert sandbox.info.mounts["network"] == [
            {"volumeId": "created-volume", "path": "/data"}
        ]
    count = len(peer.records)
    with pytest.raises(ValueError):
        await AsyncioSandbox.create(
            image="image",
            mounts={"/data": NetworkVolume("created-volume", create=False)},
            data_center_ids=["US-TX-3"],
            **peer.options,
        )
    assert len(peer.records) == count


def test_mount_and_port_collisions_are_rejected_before_provisioning(peer):
    for mounts in (
        {"/data": NetworkVolume("first"), "/other": NetworkVolume("second")},
        {"/data": NetworkVolume("first"), "/data/models": GlobalVolume("second")},
        {"/etc": GlobalVolume("second")},
        {"/data": "untyped-volume"},
    ):
        with pytest.raises((TypeError, ValueError, VolumeError)):
            Sandbox(image="image", mounts=mounts, **peer.options)
    with pytest.raises(ValueError):
        Sandbox(image="image", ports={8080: ("http", "tcp")}, **peer.options)
    assert peer.requests == []


@pytest.mark.asyncio
async def test_snapshots_preserve_unavailable_versus_empty_configuration(peer):
    async with AsyncioSandbox(image="image", **peer.options) as sandbox:
        row = peer.records[sandbox.id]
        row.update(
            env=None,
            ports=None,
            mounts=None,
            cmd=None,
            entrypoint=None,
            ssh=None,
            startedAt=None,
        )
        unavailable = await sandbox.refresh()
        assert unavailable.env is None
        assert unavailable.ports is None
        assert unavailable.mounts is None
        assert unavailable.cmd is None
        assert unavailable.entrypoint is None
        assert unavailable.ssh is None
        assert unavailable.started_at is None
        row.update(
            env={},
            ports=[],
            mounts={"network": [], "global": []},
            cmd=[],
            entrypoint=[],
            ssh={},
        )
        empty = await sandbox.refresh()
        assert empty.env == {}
        assert empty.ports == []
        assert empty.mounts == {"network": [], "global": []}
        assert empty.cmd == []
        assert empty.entrypoint == []
        assert empty.ssh == {}


@pytest.mark.asyncio
async def test_rich_exec_results_preserve_nulls_and_check_exit_status(peer):
    async with AsyncioSandbox(image="image", **peer.options) as sandbox:
        peer.exec_responses.append(
            web.json_response(
                {
                    "output": "partial",
                    "stdout": None,
                    "stderr": "",
                    "exitCode": 3,
                    "durationMs": 0,
                    "truncated": False,
                    "error": None,
                }
            )
        )
        with pytest.raises(SandboxExecutionError) as failed:
            await sandbox.exec(["fail-status"], check=True)
        result = failed.value.result
        assert result.output == "partial"
        assert result.stdout is None
        assert result.stderr == ""
        assert result.exit_code == 3
        assert result.duration_ms == 0
        assert result.truncated is False
        peer.exec_responses.append(
            web.json_response(
                {
                    "output": "partial",
                    "stdout": "partial",
                    "stderr": None,
                    "exitCode": None,
                    "durationMs": 4000,
                    "truncated": True,
                    "error": "execution timed out",
                }
            )
        )
        timed_out = await sandbox.exec(["long-command"])
        assert timed_out.exit_code is None
        assert timed_out.stderr is None
        assert timed_out.duration_ms == 4000
        assert timed_out.truncated is True
        assert timed_out.error == "execution timed out"


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["container_exited", "sandbox_terminated", None])
async def test_non_startup_conflicts_are_not_retried_even_with_running_snapshot(
    peer, code
):
    async with AsyncioSandbox(image="image", **peer.options) as sandbox:
        peer.conflict_code = code
        peer.exec_statuses.append(409)
        with pytest.raises(QueryError) as conflict:
            await sandbox.exec(["work"])
        assert conflict.value.status_code == 409
        assert peer.executed == []
        assert [path for _, path, _, _ in peer.requests] == [
            "/v2/sandboxes",
            f"/v2/sandboxes/{sandbox.id}/exec",
        ]


def test_sync_extend_uses_fresh_total_lifetime_and_update_preserves_omissions(peer):
    with Sandbox(image="image", **peer.options) as sandbox:
        peer.records[sandbox.id]["maxLifetimeSeconds"] = 1200
        extended = sandbox.extend(seconds=300)
        assert extended.max_lifetime_seconds == 1500
        assert extended.expires_at == extended.created_at + timedelta(seconds=1500)
        updated = sandbox.update(idle_timeout_seconds=600)
        assert updated.max_lifetime_seconds == 1500
        assert updated.idle_expires_at == updated.last_activity_at + timedelta(
            seconds=600
        )
        assert updated.expires_at == extended.expires_at


@pytest.mark.asyncio
async def test_async_extensions_serialize_on_one_handle(peer):
    async with AsyncioSandbox(image="image", **peer.options) as sandbox:
        await sandbox.update(max_lifetime_seconds=1200)
        await asyncio.gather(sandbox.extend(seconds=100), sandbox.extend(seconds=200))
        assert sandbox.info.max_lifetime_seconds == 1500
        assert sandbox.info.expires_at == sandbox.info.created_at + timedelta(
            seconds=1500
        )


def test_lifetime_updates_survive_close_and_event_loop_reuse(peer):
    async def first_session():
        sandbox = await AsyncioSandbox.create(image="image", **peer.options)
        try:
            await sandbox.update(max_lifetime_seconds=1200)
            await asyncio.gather(
                sandbox.extend(seconds=100), sandbox.extend(seconds=200)
            )
        finally:
            await sandbox.close()
        return sandbox

    sandbox = asyncio.run(first_session())

    async def second_session():
        try:
            await asyncio.gather(
                sandbox.extend(seconds=100), sandbox.extend(seconds=200)
            )
        finally:
            await sandbox.terminate()

    asyncio.run(second_session())
    assert sandbox.info.max_lifetime_seconds == 1800


@pytest.mark.asyncio
async def test_same_resolved_volume_id_cannot_be_mounted_as_two_kinds(peer):
    peer.network_volumes = [
        {"id": "same-id", "name": "dataset", "dataCenter": "US-KS-2"}
    ]
    peer.global_volumes = [{"id": "same-id", "name": "shared-models"}]
    with pytest.raises(ValueError):
        await AsyncioSandbox.create(
            image="image",
            mounts={
                "/data": NetworkVolume("dataset", create=False),
                "/models": GlobalVolume("same-id"),
            },
            **peer.options,
        )
    assert peer.records == {}
