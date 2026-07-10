"""deploy pipeline: discovered apps -> manifest -> artifact -> activated build."""

import base64
import fnmatch
import json
import logging
import os
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import runpod

from .api import AppsApiClient
from .app import App
from .build import (
    DEFAULT_PYTHON_VERSION,
    MAX_ARTIFACT_MB,
    BuildError,
    BuildResult,
    build_environment,
)
from .errors import InvalidResourceError, ScheduleNotSupported
from .schedule import SCHEDULES_ENABLED
from .spec import ResourceKind
from .utils.events import emit

log = logging.getLogger(__name__)

MANIFEST_VERSION = 1

DEFAULT_IGNORES = [
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "*.pyc",
    ".runpod",
    ".flash",
    "node_modules",
    ".DS_Store",
    "*.tar.gz",
]


def _module_path_for(fn, project_root: Path) -> str:
    """dotted import path for fn's file relative to the project root."""
    import inspect

    module = getattr(fn, "__module__", "") or ""
    try:
        source = Path(inspect.getfile(fn)).resolve()
        rel = source.relative_to(project_root.resolve())
    except (TypeError, ValueError, OSError):
        return module
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or module


@dataclass
class DeployResult:
    app_name: str
    build_id: str
    environment_id: str
    resources: List[str]
    endpoints: Dict[str, str] = field(default_factory=dict)


def build_manifest(
    app: App,
    project_root: Path,
    *,
    python_version: str = DEFAULT_PYTHON_VERSION,
    excluded_packages: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """serialize an app's resources for the coordinator and workers."""
    resources = []

    for handle in app.resources.values():
        spec = handle.spec
        if spec.schedule and not SCHEDULES_ENABLED:
            raise ScheduleNotSupported()

        if spec.kind in (ResourceKind.QUEUE, ResourceKind.API) and len(spec.name) < 3:
            raise InvalidResourceError(
                f"resource name '{spec.name}' is too short to deploy; "
                f"queue and api names must be at least 3 characters"
            )

        entry = spec.to_manifest()

        fn = (
            getattr(handle, "_fn", None)
            or getattr(handle, "_cls", None)
            or getattr(handle, "__wrapped__", None)
        )

        if fn is not None:
            entry["module"] = _module_path_for(fn, project_root)
            entry["qualname"] = getattr(fn, "__qualname__", fn.__name__)
        resources.append(entry)

    manifest: Dict[str, Any] = {
        "version": MANIFEST_VERSION,
        "app": app.name,
        "pythonVersion": python_version,
        "resources": resources,
    }

    if excluded_packages:
        manifest["excludedPackages"] = excluded_packages

    return manifest


def _load_ignores(project_root: Path) -> List[str]:
    patterns = list(DEFAULT_IGNORES)
    ignore_file = project_root / ".runpodignore"
    if ignore_file.exists():
        for line in ignore_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(line)
    return patterns


def _is_ignored(rel_path: str, patterns: List[str]) -> bool:
    parts = rel_path.split("/")
    for pattern in patterns:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        if any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


ENV_DIR_NAME = "env"


def package_project(
    project_root: Path,
    manifest: Dict[str, Any],
    output: Optional[Path] = None,
    env_dir: Optional[Path] = None,
) -> Path:
    """tar source + vendored env + manifest into a build artifact.

    layout inside the tarball:
        {source files}          project code, .runpodignore honored
        env/                    vendored site-packages tree
        runpod_manifest.json
    """
    if output is None:
        output = Path(tempfile.mkdtemp()) / "artifact.tar.gz"

    patterns = _load_ignores(project_root)
    env_resolved = env_dir.resolve() if env_dir is not None else None

    with tarfile.open(output, "w:gz") as tar:
        for path in sorted(project_root.rglob("*")):
            if not path.is_file():
                continue
            if env_resolved is not None and env_resolved in path.resolve().parents:
                continue
            rel = path.relative_to(project_root).as_posix()
            if _is_ignored(rel, patterns):
                continue
            if rel == ENV_DIR_NAME or rel.startswith(f"{ENV_DIR_NAME}/"):
                continue
            tar.add(path, arcname=rel)

        if env_dir is not None and env_dir.is_dir():
            for path in sorted(env_dir.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(env_dir).as_posix()
                tar.add(path, arcname=f"{ENV_DIR_NAME}/{rel}")

        manifest_bytes = json.dumps(manifest, indent=2).encode()
        info = tarfile.TarInfo(name="runpod_manifest.json")
        info.size = len(manifest_bytes)
        import io

        tar.addfile(info, io.BytesIO(manifest_bytes))

    return output


# gpu images have some hefty dependencies pre-installed
CPU_CONTAINER_DISK_GB = 10
GPU_CONTAINER_DISK_GB = 30


def _container_disk_gb(spec) -> int:
    if spec.container_disk_gb:
        return spec.container_disk_gb
    return CPU_CONTAINER_DISK_GB if spec.is_cpu else GPU_CONTAINER_DISK_GB


def _deployed_endpoint_input(
    app: App,
    spec,
    environment_id: str,
    build_id: str,
    python_version: str,
) -> Dict[str, Any]:
    """saveEndpoint payload for one deployed queue/api resource.

    the endpoint name must equal the resource name: sentinel
    resolution matches X-Flash-Endpoint against endpoint.name within
    the environment. binding via flashEnvironmentId is also what makes
    hosts deliver the build artifact to this endpoint's workers.
    """
    from .datacenter import CPU3_DATACENTERS, CPU5_DATACENTERS, DataCenter
    from .images import image_for_spec

    from .secret import render_env

    template_env = {
        "FLASH_RESOURCE_NAME": spec.name,
        # version-triggering: a new build recreates all workers
        "RUNPOD_BUILD_ID": build_id,
        **render_env(spec.env),
    }

    # cross-resource calls from inside a worker go through the sentinel,
    # which authenticates with the account api key; without it any
    # .remote() from worker code would fail
    api_key = runpod.api_key
    if api_key and "RUNPOD_API_KEY" not in template_env:
        template_env["RUNPOD_API_KEY"] = api_key

    # workers that spawn tasks must select runtime images from the same
    # channel the deploy used (the vendored env supplies the code, so
    # only the image channel needs to travel)
    tag = os.environ.get("RUNPOD_RUNTIME_TAG")
    if tag and "RUNPOD_RUNTIME_TAG" not in template_env:
        template_env["RUNPOD_RUNTIME_TAG"] = tag

    if spec.max_concurrency > 1:
        template_env["RUNPOD_MAX_CONCURRENCY"] = str(spec.max_concurrency)

    payload: Dict[str, Any] = {
        "name": spec.name,
        "flashEnvironmentId": environment_id,
        "workersMin": spec.workers[0],
        "workersMax": spec.workers[1],
        "idleTimeout": spec.idle_timeout,
        "scalerType": spec.effective_scaler_type,
        "scalerValue": spec.scaler_value,
        "executionTimeoutMs": spec.execution_timeout_ms,
        "template": {
            "name": f"{app.name}-{spec.name}-template",
            "imageName": image_for_spec(spec, python_version=python_version),
            "containerDiskInGb": _container_disk_gb(spec),
            "dockerArgs": "",
            "env": [{"key": k, "value": v} for k, v in template_env.items()],
        },
    }

    if spec.flashboot:
        payload["flashBootType"] = "FLASHBOOT"

    if spec.image:
        # custom image: inject the bootstrap; the vendored env in the
        # artifact provides the runtime once the bootstrap unpacks it
        from .dev import _bootstrap_docker_args, _bootstrap_source

        payload["template"]["env"].extend(
            [
                {
                    "key": "RUNPOD_BOOTSTRAP_B64",
                    "value": base64.b64encode(_bootstrap_source().encode()).decode(),
                },
                {"key": "RUNPOD_RUNTIME_KIND", "value": spec.kind.value},
            ]
        )
        payload["template"]["dockerArgs"] = _bootstrap_docker_args()

    if spec.kind is ResourceKind.API:
        payload["type"] = "LB"
    if spec.datacenter:
        payload["locations"] = ",".join(spec.datacenter)
    if spec.is_cpu:
        payload["instanceIds"] = spec.cpu
        if not spec.datacenter:
            if any(i.startswith("cpu5") for i in spec.cpu or []):
                payload["locations"] = ",".join(dc.value for dc in CPU5_DATACENTERS)
            else:
                payload["locations"] = ",".join(dc.value for dc in CPU3_DATACENTERS)
    else:
        from .gpu import gpu_ids_value

        payload["gpuIds"] = gpu_ids_value(spec.gpu)
        payload["gpuCount"] = spec.gpu_count
        if spec.min_cuda_version:
            payload["minCudaVersion"] = spec.min_cuda_version
        if not spec.datacenter:
            # artifact delivery rides the flash volume (network
            # storage); machines outside storage-cluster regions fail
            # with "requires network storage" and recycle forever
            payload["locations"] = ",".join(dc.value for dc in DataCenter)
    return payload


async def reconcile_endpoints(
    client: AppsApiClient,
    app: App,
    environment: Dict[str, Any],
    build_id: str,
    python_version: str,
) -> Dict[str, str]:
    """converge the environment's endpoints to the app's resources.

    resource name -> endpoint id for everything provisioned. existing
    endpoints (matched by name) are updated in place; endpoints whose
    resources disappeared are deleted.
    """
    existing = {e["name"]: e["id"] for e in environment.get("endpoints") or []}
    provisionable = {
        h.spec.name: h
        for h in app.resources.values()
        if h.spec.kind in (ResourceKind.QUEUE, ResourceKind.API)
    }

    from .registry import resolve_registry_auth
    from .volume import VolumeResolver

    resolver = VolumeResolver(client)
    endpoints: Dict[str, str] = {}
    for name, handle in sorted(provisionable.items()):
        payload = _deployed_endpoint_input(
            app, handle.spec, environment["id"], build_id, python_version
        )
        await attach_endpoint_volumes(payload, handle.spec, resolver, app)
        auth_id = await resolve_registry_auth(handle.spec.registry_auth, api=client)
        if auth_id:
            payload["template"]["containerRegistryAuthId"] = auth_id
        if handle.spec.model:
            from .model import model_reference

            payload["modelReferences"] = [model_reference(handle.spec.model)]
        if name in existing:
            payload["id"] = existing[name]
        result = await client.save_endpoint(payload)
        endpoints[name] = result["id"]
        log.info(
            "%s endpoint %s (%s)",
            "updated" if name in existing else "provisioned",
            name,
            result["id"],
        )

    for name, endpoint_id in existing.items():
        if name not in provisionable:
            await client.delete_endpoint(endpoint_id)
            log.info("deleted removed endpoint %s (%s)", name, endpoint_id)

    return endpoints


async def attach_endpoint_volumes(payload: Dict[str, Any], spec, resolver, app) -> None:
    """resolve a resource's volumes onto an endpoint payload.

    endpoints may span regions: one volume per datacenter, locations
    derived from the resolved volumes so lists cannot disagree.
    """
    from .volume import specs_sharing_volume, volume_list

    volumes = volume_list(spec.volume)
    if not volumes:
        return
    resolved = []
    for volume in volumes:
        sharing = specs_sharing_volume([app], volume.name) or [spec]
        resolved.append(await resolver.resolve(volume, sharing))
    payload["networkVolumeIds"] = [{"networkVolumeId": r["id"]} for r in resolved]
    payload["locations"] = ",".join(dict.fromkeys(r["dataCenterId"] for r in resolved))


def _phase(events, name: str, detail: str = "") -> None:
    emit(events, "phase", name, detail)


def build_artifact(
    app: App,
    project_root: Path,
    *,
    python_version: str = DEFAULT_PYTHON_VERSION,
    exclude: Optional[List[str]] = None,
    events: Optional[object] = None,
    output: Optional[Path] = None,
) -> Path:
    """vendor, build the manifest, and package one app's artifact."""
    _phase(events, "vendor", f"python {python_version}")
    build: BuildResult = build_environment(
        app,
        project_root,
        python_version=python_version,
        exclude=exclude,
        events=events,
    )
    manifest = build_manifest(
        app,
        project_root,
        python_version=python_version,
        excluded_packages=build.excluded,
    )
    _phase(events, "package")
    tar_path = package_project(
        project_root, manifest, output=output, env_dir=build.env_dir
    )
    size_mb = tar_path.stat().st_size / (1024 * 1024)
    if size_mb > MAX_ARTIFACT_MB:
        raise BuildError(
            f"artifact is {size_mb:.0f} MB (limit {MAX_ARTIFACT_MB} MB). "
            f"exclude large packages with --exclude (they must then come "
            f"from the worker image) or trim project files via .runpodignore"
        )
    log.info("packaged %s (%.1f MB)", app.name, size_mb)
    return tar_path


async def deploy_app(
    app: App,
    project_root: Path,
    *,
    env_name: Optional[str] = None,
    api: Optional[AppsApiClient] = None,
    python_version: str = DEFAULT_PYTHON_VERSION,
    exclude: Optional[List[str]] = None,
    events: Optional[object] = None,
) -> DeployResult:
    """run the full deploy pipeline for one app."""
    client = api or AppsApiClient()
    env_name = env_name or app.env

    # fail fast on unresolvable secret references (workers would boot
    # with the literal template string otherwise)
    from .secret import secret_names, validate_secrets

    referenced = [
        name
        for handle in app.resources.values()
        for name in secret_names(handle.spec.env)
    ]
    await validate_secrets(referenced, api=client)

    _phase(events, "vendor", f"python {python_version}")
    build: BuildResult = build_environment(
        app,
        project_root,
        python_version=python_version,
        exclude=exclude,
        events=events,
    )
    manifest = build_manifest(
        app,
        project_root,
        python_version=python_version,
        excluded_packages=build.excluded,
    )
    _phase(events, "package")
    tar_path = package_project(project_root, manifest, env_dir=build.env_dir)
    tar_size = tar_path.stat().st_size
    size_mb = tar_size / (1024 * 1024)
    if size_mb > MAX_ARTIFACT_MB:
        raise BuildError(
            f"artifact is {size_mb:.0f} MB (limit {MAX_ARTIFACT_MB} MB). "
            f"exclude large packages with --exclude (they must then come "
            f"from the worker image) or trim project files via .runpodignore"
        )
    log.info("packaged %s (%.1f MB)", app.name, size_mb)

    _phase(events, "upload", f"{size_mb:.1f} MB")
    upload_progress = getattr(events, "upload_progress", None)
    remote_app = await client.get_app_by_name(app.name)
    if remote_app is None:
        remote_app = await client.create_app(app.name)
        log.info("created app %s (%s)", app.name, remote_app["id"])
    app_id = remote_app["id"]

    environments = {e["name"]: e for e in remote_app.get("flashEnvironments") or []}
    environment = environments.get(env_name)
    if environment is None:
        environment = await client.create_environment(app_id, env_name)
        log.info("created environment %s (%s)", env_name, environment["id"])

    upload = await client.prepare_artifact_upload(app_id, tar_size)
    await client.upload_tarball(
        upload["uploadUrl"], str(tar_path), progress=upload_progress
    )
    build = await client.finalize_artifact_upload(app_id, upload["objectKey"], manifest)
    log.info("uploaded build %s", build["id"])

    await client.deploy_build(environment["id"], build["id"])
    log.info("activated build %s on %s/%s", build["id"], app.name, env_name)

    _phase(events, "endpoints")
    endpoints = await reconcile_endpoints(
        client, app, environment, build["id"], python_version
    )
    endpoint_ready = getattr(events, "endpoint_ready", None)
    if endpoint_ready is not None:
        for name, endpoint_id in sorted(endpoints.items()):
            endpoint_ready(name, endpoint_id)

    return DeployResult(
        app_name=app.name,
        build_id=build["id"],
        environment_id=environment["id"],
        resources=[r["name"] for r in manifest["resources"]],
        endpoints=endpoints,
    )
