"""resource specifications produced by app decorators.

a ResourceSpec is the declarative description of one deployable resource.
it is what the manifest serializes, what `rp deploy` ships to the backend,
and what dev-session provisioning consumes. it holds no live state.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from .errors import InvalidResourceError
from .gpu import GpuGroup, GpuType

DEFAULT_WORKERS: Tuple[int, int] = (0, 3)


class ResourceKind(str, Enum):
    QUEUE = "queue"
    """queue-based serverless endpoint."""

    API = "api"
    """load-balanced serverless endpoint with http routes."""

    TASK = "task"
    """ephemeral pod compute, provisioned per call."""


@dataclass(frozen=True)
class RouteSpec:
    """one http route on an api resource."""

    method: str
    path: str
    handler_name: str


def normalize_workers(workers: Union[int, Tuple[int, int], None]) -> Tuple[int, int]:
    """int n -> (0, n); (min, max) passes through; None -> default."""
    if workers is None:
        return DEFAULT_WORKERS
    if isinstance(workers, int):
        parsed = (DEFAULT_WORKERS[0], workers)
    elif isinstance(workers, (tuple, list)) and len(workers) == 2:
        parsed = (int(workers[0]), int(workers[1]))
    else:
        raise InvalidResourceError(
            f"workers must be an int or (min, max) tuple, got {workers!r}"
        )
    min_w, max_w = parsed
    if min_w < 0 or max_w < 1:
        raise InvalidResourceError(f"invalid worker range ({min_w}, {max_w})")
    if min_w > max_w:
        raise InvalidResourceError(
            f"workers min ({min_w}) cannot exceed max ({max_w})"
        )
    return (min_w, max_w)


def normalize_gpu(
    gpu: Union[GpuGroup, GpuType, str, List[Union[GpuGroup, GpuType, str]], None],
) -> Optional[List[str]]:
    """normalize gpu input to a list of api-facing string values."""
    if gpu is None:
        return None
    if not isinstance(gpu, list):
        gpu = [gpu]
    out: List[str] = []
    for g in gpu:
        if isinstance(g, (GpuGroup, GpuType)):
            out.append(g.value)
        elif isinstance(g, str):
            out.append(g)
        else:
            raise InvalidResourceError(
                f"gpu must be a GpuGroup, GpuType, or string, got {type(g).__name__}"
            )
    return out


def normalize_cpu(
    cpu: Union[str, List[str], None],
) -> Optional[List[str]]:
    """normalize cpu instance ids to a list of strings."""
    if cpu is None:
        return None
    if isinstance(cpu, str):
        return [cpu]
    if isinstance(cpu, list):
        return [str(c) for c in cpu]
    raise InvalidResourceError(
        f"cpu must be an instance id string or list, got {type(cpu).__name__}"
    )


@dataclass
class ResourceSpec:
    """declarative config for one app resource."""

    kind: ResourceKind
    name: str
    gpu: Optional[List[str]] = None
    cpu: Optional[List[str]] = None
    gpu_count: int = 1
    workers: Tuple[int, int] = DEFAULT_WORKERS
    idle_timeout: int = 60
    dependencies: Optional[List[str]] = None
    system_dependencies: Optional[List[str]] = None
    volume: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    datacenter: Optional[List[str]] = None
    image: Optional[str] = None
    schedule: Optional[str] = None
    routes: List[RouteSpec] = field(default_factory=list)
    asgi_factory: Optional[str] = None

    def __post_init__(self) -> None:
        if self.gpu is not None and self.cpu is not None:
            raise InvalidResourceError(
                f"resource '{self.name}': gpu and cpu are mutually exclusive"
            )
        if not self.name:
            raise InvalidResourceError("resource name must not be empty")

    @property
    def is_cpu(self) -> bool:
        return self.cpu is not None

    def to_manifest(self) -> Dict[str, Any]:
        """serialize for the deploy manifest."""
        data: Dict[str, Any] = {
            "kind": self.kind.value,
            "name": self.name,
            "gpuCount": self.gpu_count,
            "workersMin": self.workers[0],
            "workersMax": self.workers[1],
            "idleTimeout": self.idle_timeout,
        }
        if self.gpu is not None:
            data["gpus"] = self.gpu
        if self.cpu is not None:
            data["instanceIds"] = self.cpu
        if self.dependencies:
            data["dependencies"] = self.dependencies
        if self.system_dependencies:
            data["systemDependencies"] = self.system_dependencies
        if self.volume:
            data["networkVolume"] = self.volume
        if self.env:
            data["env"] = self.env
        if self.datacenter:
            data["locations"] = ",".join(self.datacenter)
        if self.image:
            data["imageName"] = self.image
        if self.schedule:
            data["schedule"] = self.schedule
        if self.routes:
            data["routes"] = [
                {"method": r.method, "path": r.path, "handler": r.handler_name}
                for r in self.routes
            ]
        if self.asgi_factory:
            data["asgiFactory"] = self.asgi_factory
        return data
