"""deploy pipeline: discovered apps -> manifest -> tarball -> activated build.

per app:
  1. validate resources
  2. build the manifest from the app registry
  3. package the project into a tarball (honoring .runpodignore)
  4. get-or-create the flash app + environment
  5. upload via presigned url, finalize the build
  6. activate the build on the environment (coordinator provisions)
"""

import fnmatch
import json
import logging
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .api import AppsApiClient
from .app import App
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


@dataclass
class DeployResult:
    app_name: str
    build_id: str
    environment_id: str
    resources: List[str]


def build_manifest(app: App, project_root: Path) -> Dict[str, Any]:
    """serialize an app's resources for the coordinator."""
    resources = []
    for handle in app.resources.values():
        spec = handle.spec
        if spec.schedule and not SCHEDULES_ENABLED:
            raise ScheduleNotSupported()
        entry = spec.to_manifest()
        fn = getattr(handle, "_fn", None) or getattr(handle, "__wrapped__", None)
        if fn is not None:
            module = getattr(fn, "__module__", "")
            entry["module"] = module
            entry["qualname"] = getattr(fn, "__qualname__", fn.__name__)
        resources.append(entry)
    return {
        "version": MANIFEST_VERSION,
        "app": app.name,
        "resources": resources,
    }


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


def package_project(
    project_root: Path,
    manifest: Dict[str, Any],
    output: Optional[Path] = None,
) -> Path:
    """tar the project plus the manifest into a build artifact."""
    if output is None:
        output = Path(tempfile.mkdtemp()) / "artifact.tar.gz"

    patterns = _load_ignores(project_root)

    with tarfile.open(output, "w:gz") as tar:
        for path in sorted(project_root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(project_root).as_posix()
            if _is_ignored(rel, patterns):
                continue
            tar.add(path, arcname=rel)

        manifest_bytes = json.dumps(manifest, indent=2).encode()
        info = tarfile.TarInfo(name="runpod_manifest.json")
        info.size = len(manifest_bytes)
        import io

        tar.addfile(info, io.BytesIO(manifest_bytes))

    return output


async def deploy_app(
    app: App,
    project_root: Path,
    *,
    env_name: Optional[str] = None,
    api: Optional[AppsApiClient] = None,
) -> DeployResult:
    """run the full deploy pipeline for one app."""
    client = api or AppsApiClient()
    env_name = env_name or app.env

    manifest = build_manifest(app, project_root)
    tar_path = package_project(project_root, manifest)
    tar_size = tar_path.stat().st_size
    log.info("packaged %s (%d bytes)", app.name, tar_size)

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

    return DeployResult(
        app_name=app.name,
        build_id=build["id"],
        environment_id=environment["id"],
        resources=[r["name"] for r in manifest["resources"]],
    )
