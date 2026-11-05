"""Typed snapshots and results returned by the sandbox domain."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Mapping, Optional

from runpod.error import RunPodError

SandboxState = Literal["CREATING", "RUNNING", "TERMINATED", "FAILED"]
LogSource = Literal["container", "system"]
CommandStatus = Literal[
    "running", "exited", "cancelled", "timedOut", "failed", "unknown"
]
CommandStream = Literal["stdout", "stderr"]


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class SandboxCompute:
    vcpu_count: float
    memory_in_gb: float
    container_disk_in_gb: int
    cost_per_hr: float


@dataclass(frozen=True)
class SandboxInfo:
    """A server snapshot. Reading fields does not fetch or refresh anything."""

    id: str
    name: str
    state: SandboxState
    idle_timeout_seconds: int
    max_lifetime_seconds: int
    last_activity_at: datetime
    idle_expires_at: datetime
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    template_id: Optional[str]
    image: Optional[str]
    cpu_flavor_id: Optional[str]
    termination_reason: Optional[str]
    terminated_at: Optional[datetime]
    labels: Optional[Mapping[str, str]]
    compute: Optional[SandboxCompute]
    data_center_id: Optional[str]
    env: Optional[Mapping[str, str]]
    registry: Optional[str]
    started_at: Optional[datetime]
    ssh: Optional[Mapping[str, Any]]
    ports: Optional[list[Mapping[str, Any]]]
    mounts: Optional[Mapping[str, list[Mapping[str, str]]]]
    cmd: Optional[list[str]]
    entrypoint: Optional[list[str]]
    egress_policy: Optional[Mapping[str, Any]] = None
    runtime: Optional[Mapping[str, Any]] = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SandboxInfo":
        compute = data.get("compute")
        terminated_at = data.get("terminatedAt")
        started_at = data.get("startedAt")
        return cls(
            id=data["id"],
            name=data["name"],
            state=data["state"],
            idle_timeout_seconds=data["idleTimeoutSeconds"],
            max_lifetime_seconds=data["maxLifetimeSeconds"],
            last_activity_at=_timestamp(data["lastActivityAt"]),
            idle_expires_at=_timestamp(data["idleExpiresAt"]),
            expires_at=_timestamp(data["expiresAt"]),
            created_at=_timestamp(data["createdAt"]),
            updated_at=_timestamp(data["updatedAt"]),
            template_id=data.get("templateId"),
            image=data.get("image"),
            cpu_flavor_id=(data.get("cpu") or {}).get("id"),
            termination_reason=data.get("terminationReason"),
            terminated_at=_timestamp(terminated_at) if terminated_at else None,
            labels=dict(data["labels"]) if data.get("labels") is not None else None,
            data_center_id=data.get("dataCenterId"),
            env=dict(data["env"]) if data.get("env") is not None else None,
            registry=data.get("registry"),
            started_at=_timestamp(started_at) if started_at is not None else None,
            ssh=dict(data["ssh"]) if data.get("ssh") is not None else None,
            ports=(
                [dict(port) for port in data["ports"]]
                if data.get("ports") is not None
                else None
            ),
            mounts=(
                {
                    kind: [dict(mount) for mount in mounts]
                    for kind, mounts in data["mounts"].items()
                }
                if data.get("mounts") is not None
                else None
            ),
            cmd=list(data["cmd"]) if data.get("cmd") is not None else None,
            entrypoint=(
                list(data["entrypoint"]) if data.get("entrypoint") is not None else None
            ),
            egress_policy=(
                dict(data["egressPolicy"])
                if data.get("egressPolicy") is not None
                else None
            ),
            runtime=dict(data["runtime"]) if data.get("runtime") is not None else None,
            compute=(
                SandboxCompute(
                    vcpu_count=compute["vcpuCount"],
                    memory_in_gb=compute["memoryInGb"],
                    container_disk_in_gb=compute["containerDiskInGb"],
                    cost_per_hr=compute["costPerHr"],
                )
                if compute is not None
                else None
            ),
        )


@dataclass(frozen=True)
class ExecResult:
    """command output with nullable host-reported execution details."""

    output: str
    error: Optional[str] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    exit_code: Optional[int] = None
    duration_ms: Optional[int] = None
    truncated: Optional[bool] = None


@dataclass(frozen=True)
class LogEvent:
    """One log record; preserve the opaque SSE id for subsequent resume requests."""

    source: LogSource
    line: str
    timestamp: datetime
    id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LogEvent":
        return cls(
            source=data["source"],
            line=data["line"],
            timestamp=_timestamp(data["ts"]),
            id=data.get("id"),
        )


@dataclass(frozen=True)
class CommandInfo:
    """A snapshot of a started command. Read the outcome from ``status``, not ``exit_code``."""

    id: str
    status: CommandStatus
    exit_code: Optional[int]
    error: Optional[str]
    streams_separated: bool
    output_bytes: int
    truncated: bool
    cancel_requested: bool

    @property
    def done(self) -> bool:
        return self.status != "running"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CommandInfo":
        return cls(
            id=data["id"],
            status=data["status"],
            exit_code=data.get("exitCode"),
            error=data.get("error"),
            streams_separated=bool(data.get("streamsSeparated", False)),
            output_bytes=data.get("outputBytes", 0),
            truncated=bool(data.get("truncated", False)),
            cancel_requested=bool(data.get("cancelRequested", False)),
        )


@dataclass(frozen=True)
class CommandOutput:
    """One page of a command's stored output.

    Offsets are positions in stored output, not indexes into ``output``: pass
    ``next_offset`` back unchanged to continue. The command has finished only
    when ``eof`` is true and its status is terminal.
    """

    output: str
    offset: int
    next_offset: int
    eof: bool

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CommandOutput":
        return cls(
            output=data["output"],
            offset=data["offset"],
            next_offset=data["nextOffset"],
            eof=data["eof"],
        )


@dataclass(frozen=True)
class CommandLogLine:
    """One streamed output line; ``partial`` marks a line the host cut at 64 KiB."""

    line: str
    stream: Optional[CommandStream] = None
    timestamp: Optional[datetime] = None
    partial: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CommandLogLine":
        ts = data.get("ts")
        return cls(
            line=data["line"],
            stream=data.get("stream") or None,
            timestamp=_timestamp(ts) if ts else None,
            partial=bool(data.get("partial", False)),
        )


@dataclass(frozen=True)
class CommandLogEnd:
    """The final event of a command's log stream."""

    status: CommandStatus
    exit_code: Optional[int]
    output_bytes: int
    truncated: bool

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CommandLogEnd":
        return cls(
            status=data["status"],
            exit_code=data.get("exitCode"),
            output_bytes=data.get("outputBytes", 0),
            truncated=bool(data.get("truncated", False)),
        )


class SandboxExecutionError(RunPodError):
    """A command failed with check=True; its partial output remains available."""

    def __init__(self, sandbox_id: str, result: ExecResult):
        super().__init__(
            result.error or f"Sandbox command exited with status {result.exit_code}"
        )
        self.sandbox_id = sandbox_id
        self.result = result


class SandboxStateError(RunPodError):
    """An operation cannot proceed because the sandbox reached a terminal state."""

    def __init__(self, info: SandboxInfo):
        super().__init__(
            f"Sandbox {info.id} is {info.state}"
            + (f": {info.termination_reason}" if info.termination_reason else "")
        )
        self.info = info


class SandboxStartupTimeout(TimeoutError):
    """The sandbox did not accept the operation within the startup deadline."""

    def __init__(self, sandbox_id: str, timeout: float):
        super().__init__(
            f"Sandbox {sandbox_id} did not become ready within {timeout:g}s"
        )
        self.sandbox_id = sandbox_id
        self.timeout = timeout
