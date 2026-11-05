"""Long-running commands and per-command exec options against the REST peer."""

import asyncio

import pytest

from runpod import AsyncioSandbox, Sandbox
from runpod.api.sandboxes import SandboxConflictError
from runpod.sandbox import (
    CommandInfo,
    CommandLogEnd,
    CommandLogLine,
    SandboxInfo,
)
from tests.test_sandbox import SandboxService, sandbox_peer


def test_sync_exec_sends_cwd_and_env():
    with sandbox_peer() as peer:
        with Sandbox(**peer.options) as sandbox:
            sandbox.exec(["pwd"], cwd="/workspace", env={"FOO": "bar"})
            sandbox.exec(["pwd"])
        assert peer.exec_bodies == [
            {"command": ["pwd"], "cwd": "/workspace", "env": {"FOO": "bar"}},
            {"command": ["pwd"]},
        ]


@pytest.mark.parametrize("api", [Sandbox, AsyncioSandbox])
@pytest.mark.parametrize(
    "options, error",
    [
        ({"cwd": "workspace"}, ValueError),
        ({"env": {"FOO": 1}}, TypeError),
        ({"cwd": "/tmp", "background": True}, ValueError),
        ({"env": {"FOO": "bar"}, "background": True}, ValueError),
    ],
)
def test_invalid_exec_options_send_nothing(api, options, error):
    with sandbox_peer() as peer:
        if api is Sandbox:
            with Sandbox(**peer.options) as sandbox:
                with pytest.raises(error):
                    sandbox.exec(["pwd"], **options)
        else:

            async def run():
                async with AsyncioSandbox(**peer.options) as sandbox:
                    with pytest.raises(error):
                        await sandbox.exec(["pwd"], **options)

            asyncio.run(run())
        assert peer.exec_bodies == []


def test_sync_command_lifecycle():
    with sandbox_peer() as peer:
        with Sandbox(**peer.options) as sandbox:
            command = sandbox.start_command(
                ["sh", "-c", "make test"],
                cwd="/workspace",
                env={"CI": "1"},
                timeout_seconds=1800,
            )
            assert command.status == "running"
            assert command.info.streams_separated is True

            with command.logs() as stream:
                lines = list(stream)
            assert lines == [
                CommandLogLine(
                    line="hello",
                    stream="stdout",
                    timestamp=lines[0].timestamp,
                    partial=False,
                ),
                CommandLogLine(line="wor", partial=True),
            ]
            assert lines[0].timestamp.isoformat() == "2026-11-05T12:00:00+00:00"
            assert stream.end == CommandLogEnd(
                status="exited", exit_code=0, output_bytes=12, truncated=False
            )
            assert stream.next_offset is None

            info = command.wait(poll_interval=0.01)
            assert (info.status, info.exit_code, info.done) == ("exited", 0, True)

            page = command.output()
            assert (page.output, page.offset, page.next_offset, page.eof) == (
                "hello\nworld\n",
                0,
                12,
                True,
            )
            assert command.output(offset=6).output == "world\n"

            assert sandbox.get_command(command.id).info == info
        assert peer.command_bodies == [
            {
                "command": ["sh", "-c", "make test"],
                "timeoutSeconds": 1800,
                "cwd": "/workspace",
                "env": {"CI": "1"},
            }
        ]
        log_requests = [r for r in peer.requests if r[1].endswith("/logs")]
        assert log_requests, "expected the log endpoints to be requested"


@pytest.mark.asyncio
async def test_async_command_cancel_and_wait_timeout():
    with sandbox_peer() as peer:
        async with AsyncioSandbox(**peer.options) as sandbox:
            command = await sandbox.start_command(["sleep"])
            with pytest.raises(TimeoutError, match="still running"):
                await command.wait(timeout=0.05, poll_interval=0.01)
            info = await command.cancel()
            assert (info.status, info.cancel_requested) == ("cancelled", True)
            assert (await command.cancel()).status == "cancelled"
            assert (await command.wait()).status == "cancelled"


@pytest.mark.asyncio
async def test_async_stream_timeout_leaves_resume_offset():
    with sandbox_peer() as peer:
        peer.command_stream_ending = "timeout"
        async with AsyncioSandbox(**peer.options) as sandbox:
            command = await sandbox.start_command(["build"])
            async with command.logs(offset=0) as stream:
                lines = [line.line async for line in stream]
            assert lines == ["hello", "wor"]
            assert stream.end is None
            assert stream.next_offset == 6


@pytest.mark.asyncio
async def test_command_that_cannot_start_is_returned_failed():
    with sandbox_peer() as peer:
        async with AsyncioSandbox(**peer.options) as sandbox:
            command = await sandbox.start_command(["cannot-start"])
            assert command.info == CommandInfo(
                id="cmd-1",
                status="failed",
                exit_code=None,
                error="executable not found",
                streams_separated=True,
                output_bytes=0,
                truncated=False,
                cancel_requested=False,
            )


@pytest.mark.asyncio
async def test_command_start_retries_only_while_the_sandbox_starts():
    with sandbox_peer() as peer:
        async with AsyncioSandbox(**peer.options) as sandbox:
            peer.command_statuses.extend([409, 409])
            command = await sandbox.start_command(["build"])
            assert command.status == "running"
            assert len(peer.command_bodies) == 1

            peer.conflict_code = "command_limit_reached"
            peer.command_statuses.append(409)
            with pytest.raises(SandboxConflictError) as raised:
                await sandbox.start_command(["build"])
            assert raised.value.code == "command_limit_reached"
            assert len(peer.command_bodies) == 1


@pytest.mark.asyncio
async def test_streams_filter_on_merged_output_is_a_conflict():
    with sandbox_peer() as peer:
        async with AsyncioSandbox(**peer.options) as sandbox:
            command = await sandbox.start_command(["merged"])
            assert command.info.streams_separated is False
            with pytest.raises(SandboxConflictError):
                await command.output(streams="stderr")


@pytest.mark.parametrize(
    "call, error",
    [
        (lambda s: s.start_command("make test"), TypeError),
        (lambda s: s.start_command([]), ValueError),
        (lambda s: s.start_command(["x"], timeout_seconds=0), ValueError),
        (lambda s: s.start_command(["x"], timeout_seconds=86401), ValueError),
        (lambda s: s.start_command(["x"], cwd="relative"), ValueError),
    ],
)
def test_invalid_command_options_send_nothing(call, error):
    with sandbox_peer() as peer:
        with Sandbox(**peer.options) as sandbox:
            with pytest.raises(error):
                call(sandbox)
        assert peer.command_bodies == []


@pytest.mark.parametrize(
    "options",
    [{"offset": -1}, {"tail": 0}, {"tail": 2_097_153}, {"streams": "both"}],
)
def test_invalid_output_options_are_rejected_locally(options):
    with sandbox_peer() as peer:
        with Sandbox(**peer.options) as sandbox:
            command = sandbox.start_command(["build"])
            requests = len(peer.requests)
            with pytest.raises(ValueError):
                command.output(**options)
            assert len(peer.requests) == requests


def test_snapshot_reads_runtime_and_egress_policy():
    record = SandboxService().create_record({})
    record["runtime"] = {"uptimeInSeconds": 12, "container": {"cpuPercent": 3}}
    record["egressPolicy"] = {"mode": "ALLOW_ALL", "allow": []}
    info = SandboxInfo.from_dict(record)
    assert info.runtime == record["runtime"]
    assert info.egress_policy == record["egressPolicy"]
    bare = SandboxService().create_record({})
    assert SandboxInfo.from_dict(bare).runtime is None
