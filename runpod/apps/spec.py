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

SCALER_TYPES = frozenset({"QUEUE_DELAY", "REQUEST_COUNT"})
DEFAULT_SCALER_VALUE = 4

# cuda versions the platform can filter hosts by
CUDA_VERSIONS = frozenset(
    {
        "11.8",
        "12.0",
        "12.1",
        "12.2",
        "12.3",
        "12.4",
        "12.5",
        "12.6",
        "12.7",
        "12.8",
        "12.9",
        "13.0",
    }
)


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
    from .gpu import resolve_gpu_string

    out: List[str] = []
    for g in gpu:
        if isinstance(g, (GpuGroup, GpuType)):
            out.append(g.value)
        elif isinstance(g, str):
            # strings resolve strictly: pool ids and device names pass
            # through, shorthands ("4090", "B200") expand, and typos
            # fail at decoration time instead of at the api
            out.extend(resolve_gpu_string(g))
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


def normalize_scaler_type(scaler_type: Optional[str]) -> Optional[str]:
    """validate a scaler type string, tolerating lowercase."""
    if scaler_type is None:
        return None
    normalized = str(scaler_type).strip().upper()
    if normalized not in SCALER_TYPES:
        raise InvalidResourceError(
            f"scaler_type must be one of {sorted(SCALER_TYPES)}, "
            f"got {scaler_type!r}"
        )
    return normalized


def normalize_cuda_version(version: Optional[str]) -> Optional[str]:
    """validate a minimum cuda version string."""
    if version is None:
        return None
    normalized = str(version).strip()
    if normalized not in CUDA_VERSIONS:
        raise InvalidResourceError(
            f"min_cuda_version must be one of "
            f"{', '.join(sorted(CUDA_VERSIONS))}, got {version!r}"
        )
    return normalized


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
    volume: Optional[Any] = None
    env: Optional[Dict[str, Any]] = None
    datacenter: Optional[List[str]] = None
    image: Optional[str] = None
    registry_auth: Optional[str] = None
    model: Optional[Any] = None
    schedule: Optional[str] = None
    max_concurrency: int = 1
    execution_timeout_ms: int = 0
    flashboot: bool = True
    scaler_type: Optional[str] = None
    scaler_value: int = DEFAULT_SCALER_VALUE
    min_cuda_version: Optional[str] = None
    accelerate_downloads: bool = True
    container_disk_gb: Optional[int] = None
    routes: List[RouteSpec] = field(default_factory=list)
    asgi_factory: Optional[str] = None

    def __post_init__(self) -> None:
        if self.gpu is not None and self.cpu is not None:
            raise InvalidResourceError(
                f"resource '{self.name}': gpu and cpu are mutually exclusive"
            )
        if not self.name:
            raise InvalidResourceError("resource name must not be empty")
        if self.max_concurrency < 1:
            raise InvalidResourceError(
                f"resource '{self.name}': max_concurrency must be >= 1, "
                f"got {self.max_concurrency}"
            )
        if self.execution_timeout_ms < 0:
            raise InvalidResourceError(
                f"resource '{self.name}': execution_timeout_ms must be >= 0"
            )
        if self.scaler_value < 1:
            raise InvalidResourceError(
                f"resource '{self.name}': scaler_value must be >= 1"
            )
        if self.container_disk_gb is not None and self.container_disk_gb < 1:
            raise InvalidResourceError(
                f"resource '{self.name}': container_disk_gb must be >= 1"
            )
        if self.min_cuda_version is not None and self.is_cpu:
            raise InvalidResourceError(
                f"resource '{self.name}': min_cuda_version has no effect "
                f"on cpu resources"
            )
        if self.model is not None and self.kind is ResourceKind.TASK:
            raise InvalidResourceError(
                f"resource '{self.name}': platform-cached models are only "
                f"available on queue and api resources; tasks download "
                f"weights themselves"
            )

    @property
    def is_cpu(self) -> bool:
        return self.cpu is not None

    @property
    def effective_scaler_type(self) -> str:
        """the scaler the endpoint deploys with: explicit or kind default."""
        if self.scaler_type:
            return self.scaler_type
        return (
            "REQUEST_COUNT" if self.kind is ResourceKind.API else "QUEUE_DELAY"
        )

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
            data["networkVolume"] = getattr(
                self.volume, "name", None
            ) or str(self.volume)
        if self.env:
            from .secret import render_env

            data["env"] = render_env(self.env)
        if self.datacenter:
            data["locations"] = ",".join(self.datacenter)
        if self.image:
            data["imageName"] = self.image
        if self.registry_auth:
            data["registryAuth"] = self.registry_auth
        if self.model:
            from .model import model_reference

            data["model"] = model_reference(self.model)
        if self.schedule:
            data["schedule"] = self.schedule
        if self.max_concurrency != 1:
            data["maxConcurrency"] = self.max_concurrency
        if self.execution_timeout_ms:
            data["executionTimeoutMs"] = self.execution_timeout_ms
        if not self.flashboot:
            data["flashboot"] = False
        if self.scaler_type:
            data["scalerType"] = self.scaler_type
        if self.scaler_value != DEFAULT_SCALER_VALUE:
            data["scalerValue"] = self.scaler_value
        if self.min_cuda_version:
            data["minCudaVersion"] = self.min_cuda_version
        if not self.accelerate_downloads:
            data["accelerateDownloads"] = False
        if self.container_disk_gb:
            data["containerDiskGb"] = self.container_disk_gb
        if self.routes:
            data["routes"] = [
                {"method": r.method, "path": r.path, "handler": r.handler_name}
                for r in self.routes
            ]
        if self.asgi_factory:
            data["asgiFactory"] = self.asgi_factory
        return data
