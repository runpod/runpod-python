"""runtime image selection.

one place maps (resource kind, gpu/cpu, python version) to the runtime
image built by the runtimes workflow. cpu images are python:X.Y-slim
based; gpu images additionally preinstall the torch family (matching
the build-time exclusion set) so deployed artifacts never install it
at cold start.

RUNPOD_RUNTIME_TAG selects the image channel (latest, dev, or a pinned
version).
"""

import os
import sys
from typing import Optional

from .spec import ResourceKind, ResourceSpec

# policy: non-EOL cpython versions with torch wheel support. 3.10
# ages out at its 2026-10 EOL; new versions join once torch ships
# wheels for them.
SUPPORTED_PYTHON_VERSIONS = ("3.10", "3.11", "3.12", "3.13", "3.14")
DEFAULT_PYTHON_VERSION = "3.12"

_REPOS = {
    (ResourceKind.QUEUE, False): "runpod/queue",
    (ResourceKind.QUEUE, True): "runpod/queue-gpu",
    (ResourceKind.API, False): "runpod/api",
    (ResourceKind.API, True): "runpod/api-gpu",
    (ResourceKind.TASK, False): "runpod/task",
    (ResourceKind.TASK, True): "runpod/task-gpu",
}


def runtime_tag() -> str:
    return os.environ.get("RUNPOD_RUNTIME_TAG", "latest")


def local_python_version() -> str:
    """the local interpreter's version, which the worker must match.

    dev sessions and tasks ship cloudpickle payloads produced on the
    client interpreter; those are not reliably portable across python
    versions, so a silent fallback would surface as cryptic
    deserialization failures on the worker. mismatches fail here,
    loudly, instead.
    """
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    if version not in SUPPORTED_PYTHON_VERSIONS:
        raise RuntimeError(
            f"python {version} has no runtime image "
            f"(supported: {', '.join(SUPPORTED_PYTHON_VERSIONS)}). "
            f"run under a supported python or use a custom image= built "
            f"for {version}."
        )
    return version


def runtime_image(
    kind: ResourceKind,
    *,
    gpu: bool,
    python_version: Optional[str] = None,
) -> str:
    """the builtin runtime image for a resource shape."""
    version = python_version or DEFAULT_PYTHON_VERSION
    if version not in SUPPORTED_PYTHON_VERSIONS:
        raise ValueError(
            f"python {version} is not supported by the runtime images "
            f"(supported: {', '.join(SUPPORTED_PYTHON_VERSIONS)})"
        )
    repo = _REPOS[(kind, gpu)]
    return f"{repo}:py{version}-{runtime_tag()}"


def image_for_spec(
    spec: ResourceSpec, *, python_version: Optional[str] = None
) -> str:
    """the image a resource runs on: its custom image or the builtin."""
    if spec.image:
        return spec.image
    return runtime_image(
        spec.kind, gpu=not spec.is_cpu, python_version=python_version
    )
