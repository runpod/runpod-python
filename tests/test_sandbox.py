"""Sandbox lifecycle and streaming regressions over an actual HTTP connection."""

import asyncio
import json
import subprocess
import sys
import threading
from collections import deque
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from runpod import AsyncioSandbox, Sandbox
from runpod.error import QueryError
from runpod.sandbox import (
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
        self.log_statuses = deque()
        self.delete_status = 204
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
            "compute": {
                "vcpuCount": body.get("vcpuCount", 2),
                "memoryInGb": body.get("memoryInGb", 4),
                "containerDiskInGb": 10,
                "costPerHr": 0.026,
            },
        }
        self.records[sandbox_id] = record
        return record


@contextmanager
def sandbox_peer():
    service = SandboxService()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def reply(self, status, data=None):
            payload = json.dumps(data).encode() if data is not None else b""
            self.send_response(status)
            self.send_header(
                "Content-Type",
                "application/problem+json" if status >= 400 else "application/json",
            )
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def handle_request(self):
            parsed = urlsplit(self.path)
            segments = [unquote(part) for part in parsed.path.split("/") if part]
            content_length = int(self.headers.get("Content-Length", "0"))
            body = (
                json.loads(self.rfile.read(content_length)) if content_length else None
            )
            service.requests.append(
                (self.command, parsed.path, body, dict(self.headers))
            )
            if self.headers.get("Authorization") != "Bearer sandbox-test-key":
                self.reply(401, {"detail": "invalid key"})
                return
            if segments == ["v2", "sandboxes"]:
                if self.command == "POST":
                    record = service.create_record(body)
                    service.create_started.set()
                    service.allow_create.wait(5)
                    self.reply(201, record)
                else:
                    query = parse_qs(parsed.query)
                    state = query.get("state", [None])[0]
                    records = [
                        record
                        for record in service.records.values()
                        if (
                            record["state"] == state
                            if state
                            else record["state"] != "TERMINATED"
                        )
                    ]
                    for label in query.get("labels", []):
                        key, value = label.split("=", 1)
                        records = [
                            record
                            for record in records
                            if record["labels"].get(key) == value
                        ]
                    self.reply(200, {"sandboxes": records})
                return
            if (
                len(segments) < 3
                or segments[:2] != ["v2", "sandboxes"]
                or segments[2] not in service.records
            ):
                self.reply(404, {"detail": "missing sandbox"})
                return
            record = service.records[segments[2]]
            if len(segments) == 3:
                if self.command == "DELETE":
                    if service.delete_status == 204:
                        record.update(
                            state="TERMINATED",
                            terminatedAt="2026-09-14T12:01:00Z",
                            terminationReason="USER",
                            compute=None,
                        )
                        self.reply(204)
                    else:
                        self.reply(
                            service.delete_status, {"detail": "cleanup unavailable"}
                        )
                else:
                    self.reply(200, record)
            elif segments[3] == "exec":
                status = (
                    service.exec_statuses.popleft() if service.exec_statuses else 200
                )
                if record["state"] in ("FAILED", "TERMINATED"):
                    status = 409
                if status == 409:
                    self.reply(409, {"detail": "container not started"})
                    return
                service.executed.append(body["command"])
                if status >= 400:
                    self.reply(
                        status, {"detail": "response lost after command execution"}
                    )
                    return
                if body["command"] == ["fail"]:
                    self.reply(
                        200, {"output": "partial output", "error": "command failed"}
                    )
                else:
                    self.reply(200, {"output": "completed", "error": None})
            elif segments[3] == "logs":
                service.log_started.set()
                service.allow_logs.wait(5)
                status = service.log_statuses.popleft() if service.log_statuses else 200
                if status != 200:
                    self.reply(status, {"detail": "logs not ready"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                try:
                    # Byte-at-a-time writes ensure parser state is exercised,
                    # regardless of how the client batches socket reads.
                    for byte in service.log_bytes:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                    while service.hold_logs and not service.stopping.wait(0.01):
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    service.log_disconnected.set()

        do_GET = handle_request
        do_POST = handle_request
        do_DELETE = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    service.options = {
        "api_key": "sandbox-test-key",
        "base_url": f"http://127.0.0.1:{server.server_port}",
        "request_timeout": 2,
        "startup_timeout": 2,
    }
    try:
        yield service
    finally:
        service.allow_create.set()
        service.stopping.set()
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def peer():
    with sandbox_peer() as service:
        yield service


def test_sync_startup_conflict_and_borrowed_context_ownership(peer):
    peer.exec_statuses.extend([409, 200])
    with Sandbox(image_name="python:3.12-slim", **peer.options) as owner:
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
    async with AsyncioSandbox(image_name="python:3.12-slim", **peer.options) as owner:
        async with await AsyncioSandbox.get(owner.id, **peer.options) as borrowed:
            assert (await borrowed.exec(["work"])).output == "completed"
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
        async with AsyncioSandbox(image_name="python:3.12-slim", **peer.options):
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
        Sandbox.create(image_name="python:3.12-slim", **peer.options)
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
    with pytest.raises(failure_type) as raised:
        with Sandbox(image_name="python:3.12-slim", **peer.options):
            raise failure
    assert raised.value is failure
    if delete_status == 204:
        assert peer.records["sandbox-1"]["state"] == "TERMINATED"
    else:
        assert isinstance(raised.value.__cause__, QueryError)
        assert raised.value.__cause__.status_code == 503
        assert peer.records["sandbox-1"]["state"] == "RUNNING"


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_status", [204, 503])
async def test_async_body_exception_remains_primary_during_cleanup(peer, delete_status):
    peer.delete_status = delete_status
    failure = ValueError("application failed")
    with pytest.raises(ValueError) as raised:
        async with AsyncioSandbox(image_name="python:3.12-slim", **peer.options):
            raise failure
    assert raised.value is failure
    if delete_status == 204:
        assert peer.records["sandbox-1"]["state"] == "TERMINATED"
    else:
        assert isinstance(raised.value.__cause__, QueryError)
        assert raised.value.__cause__.status_code == 503


@pytest.mark.asyncio
async def test_exec_never_replays_ambiguous_failure_and_preserves_partial_output(peer):
    async with AsyncioSandbox(image_name="python:3.12-slim", **peer.options) as sandbox:
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
async def test_startup_deadline_and_terminal_state_do_not_execute_commands(peer):
    async with AsyncioSandbox(image_name="python:3.12-slim", **peer.options) as sandbox:
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
    with Sandbox(image_name="python:3.12-slim", **peer.options) as sandbox:
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
    async with AsyncioSandbox(image_name="python:3.12-slim", **peer.options) as sandbox:
        async with sandbox.logs() as logs:
            event = await logs.__anext__()
            assert event.line == "caf\u00e9"
        assert await asyncio.to_thread(peer.log_disconnected.wait, 2)


@pytest.mark.asyncio
async def test_closing_sandbox_cancels_pending_log_handshake(peer):
    peer.allow_logs.clear()
    sandbox = await AsyncioSandbox.create(image_name="python:3.12-slim", **peer.options)
    logs = sandbox.logs()
    opening = asyncio.create_task(logs.open())
    try:
        assert await asyncio.to_thread(peer.log_started.wait, 2)
        await asyncio.wait_for(sandbox.close(), 3)
        with pytest.raises(asyncio.CancelledError):
            await opening
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
