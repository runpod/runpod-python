"""Typed snapshots and results returned by the sandbox domain."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Mapping, Optional

from runpod.error import RunPodError

SandboxState = Literal["CREATING", "RUNNING", "TERMINATED", "FAILED"]
LogSource = Literal["container", "system"]


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class SandboxCompute:
    vcpu_count: int
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
    image_name: Optional[str]
    cpu_flavor_id: Optional[str]
    termination_reason: Optional[str]
    terminated_at: Optional[datetime]
    labels: Mapping[str, str]
    compute: Optional[SandboxCompute]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SandboxInfo":
        compute = data.get("compute")
        terminated_at = data.get("terminatedAt")
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
            image_name=data.get("imageName"),
            cpu_flavor_id=data.get("cpuFlavorId"),
            termination_reason=data.get("terminationReason"),
            terminated_at=_timestamp(terminated_at) if terminated_at else None,
            labels=dict(data.get("labels") or {}),
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
    """Combined command output and the host's optional failure description."""

    output: str
    error: Optional[str] = None


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


class SandboxExecutionError(RunPodError):
    """A command failed with check=True; its partial output remains available."""

    def __init__(self, sandbox_id: str, result: ExecResult):
        super().__init__(result.error)
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
