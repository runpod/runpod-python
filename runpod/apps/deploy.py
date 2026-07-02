"""deploy pipeline: discovered apps -> manifest -> artifact -> activated build.

per app:
  1. validate resources
  2. build the manifest from the app registry
  3. vendor the runtime environment (runpod + all deps, resolved for
     the worker platform) so cold starts install nothing
  4. package source + env + manifest into a tarball (.runpodignore
     honored for source)
  5. get-or-create the flash app + environment
  6. upload via presigned url, finalize the build
  7. activate the build on the environment (hosts deliver the artifact
     to workers via the flash volume)
  8. reconcile endpoints: one per queue/api resource, named exactly
     the resource name and bound to the environment via
     flashEnvironmentId (the relation the sentinel's
     resolveFlashEndpoint walks). removed resources' endpoints are
     deleted; tasks have no standing infra.
"""

import base64
import fnmatch
import json
import logging
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .api import AppsApiClient
from .app import App
from .build import (
    DEFAULT_PYTHON_VERSION,
    MAX_ARTIFACT_MB,
    BuildError,
    BuildResult,
    build_environment,
)
from .errors import ScheduleNotSupported
from .schedule import SCHEDULES_ENABLED
from .spec import ResourceKind

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
    """dotted import path for fn's file relative to the project root.

    discovery imports files under synthetic module names, so
    fn.__module__ is meaningless on the worker; the import path must be
    derived from where the file sits in the shipped tree.
    """
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
        # packages the build expects from the worker image rather than
        # the vendored env (size-prohibitive CUDA packages)
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
    from .datacenter import CPU3_DATACENTERS, CPU5_DATACENTERS
    from .images import image_for_spec

    template_env = {
        "FLASH_RESOURCE_NAME": spec.name,
        # version-triggering: a new build recreates all workers
        "RUNPOD_BUILD_ID": build_id,
        **(spec.env or {}),
    }

    payload: Dict[str, Any] = {
        "name": spec.name,
        "flashEnvironmentId": environment_id,
        "workersMin": spec.workers[0],
        "workersMax": spec.workers[1],
        "idleTimeout": spec.idle_timeout,
        "scalerType": (
            "REQUEST_COUNT" if spec.kind is ResourceKind.API else "QUEUE_DELAY"
        ),
        "scalerValue": 4,
        "flashBootType": "FLASHBOOT",
        "template": {
            "name": f"{app.name}-{spec.name}-template",
            "imageName": image_for_spec(spec, python_version=python_version),
            "containerDiskInGb": 10,
            "dockerArgs": "",
            "env": [
                {"key": k, "value": v} for k, v in template_env.items()
            ],
        },
    }

    if spec.image:
        # custom image: inject the bootstrap; the vendored env in the
        # artifact provides the runtime once the bootstrap unpacks it
        from .dev import _bootstrap_docker_args, _bootstrap_source

        payload["template"]["env"].extend(
            [
                {
                    "key": "RUNPOD_BOOTSTRAP_B64",
                    "value": base64.b64encode(
                        _bootstrap_source().encode()
                    ).decode(),
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
                payload["locations"] = ",".join(
                    dc.value for dc in CPU5_DATACENTERS
                )
            else:
                payload["locations"] = ",".join(
                    dc.value for dc in CPU3_DATACENTERS
                )
    else:
        payload["gpuIds"] = ",".join(spec.gpu or ["any"])
        payload["gpuCount"] = spec.gpu_count
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
    existing = {
        e["name"]: e["id"] for e in environment.get("endpoints") or []
    }
    provisionable = {
        h.spec.name: h
        for h in app.resources.values()
        if h.spec.kind in (ResourceKind.QUEUE, ResourceKind.API)
    }

    endpoints: Dict[str, str] = {}
    for name, handle in sorted(provisionable.items()):
        payload = _deployed_endpoint_input(
            app, handle.spec, environment["id"], build_id, python_version
        )
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


async def deploy_app(
    app: App,
    project_root: Path,
    *,
    env_name: Optional[str] = None,
    api: Optional[AppsApiClient] = None,
    python_version: str = DEFAULT_PYTHON_VERSION,
    exclude: Optional[List[str]] = None,
) -> DeployResult:
    """run the full deploy pipeline for one app."""
    client = api or AppsApiClient()
    env_name = env_name or app.env

    build: BuildResult = build_environment(
        app, project_root, python_version=python_version, exclude=exclude
    )
    manifest = build_manifest(
        app,
        project_root,
        python_version=python_version,
        excluded_packages=build.excluded,
    )
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

    remote_app = await client.get_app_by_name(app.name)
    if remote_app is None:
        remote_app = await client.create_app(app.name)
        log.info("created app %s (%s)", app.name, remote_app["id"])
    app_id = remote_app["id"]

    environments = {
        e["name"]: e for e in remote_app.get("flashEnvironments") or []
    }
    environment = environments.get(env_name)
    if environment is None:
        environment = await client.create_environment(app_id, env_name)
        log.info("created environment %s (%s)", env_name, environment["id"])

    upload = await client.prepare_artifact_upload(app_id, tar_size)
    await client.upload_tarball(upload["uploadUrl"], str(tar_path))
    build = await client.finalize_artifact_upload(
        app_id, upload["objectKey"], manifest
    )
    log.info("uploaded build %s", build["id"])

    await client.deploy_build(environment["id"], build["id"])
    log.info("activated build %s on %s/%s", build["id"], app.name, env_name)

    endpoints = await reconcile_endpoints(
        client, app, environment, build["id"], python_version
    )

    return DeployResult(
        app_name=app.name,
        build_id=build["id"],
        environment_id=environment["id"],
        resources=[r["name"] for r in manifest["resources"]],
        endpoints=endpoints,
    )
