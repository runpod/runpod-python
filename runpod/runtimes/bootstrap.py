"""stdlib-only bootstrap for serverless endpoints on custom images.

deployed code reaches the pod via the host's artifact delivery (any pod
in a flash environment gets the build tarball, regardless of image).
this shim makes that code runnable on a bare image:

  phase unpack     wait for and extract the artifact into /app
  phase runtime    pip install the runpod package if missing
  phase deps       pip install this resource's manifest dependencies
  phase worker     exec the real runtime: runpod.runtimes.queue.worker
                   or runpod.runtimes.api.server, selected by
                   RUNPOD_RUNTIME_KIND (queue | api)

if any phase fails, instead of crash-looping silently, a minimal
job-take loop starts and answers every job with the structured error so
the failure is visible in job responses, not just container logs.

this module is the entrypoint for every serverless runtime image
(runpod/queue, runpod/api) and for custom images, where it is injected
base64 in a template env var and booted via dockerArgs. stdlib only;
must run on any image with a python3 binary.
"""

import json
import os
import subprocess
import sys
import tarfile
import time
import urllib.request

ARTIFACT_PATH = os.environ.get(
    "FLASH_BUILD_ARTIFACT_PATH", "/root/.runpod/artifact.tar.gz"
)
APP_DIR = os.environ.get("RUNPOD_APP_DIR", "/app")
ARTIFACT_WAIT_SECONDS = int(os.environ.get("RUNPOD_ARTIFACT_WAIT", "300"))
MANIFEST_NAME = "runpod_manifest.json"


def _log(message):
    sys.stderr.write(f"[bootstrap] {message}\n")
    sys.stderr.flush()


class PhaseError(Exception):
    def __init__(self, phase, detail):
        self.phase = phase
        self.detail = detail
        super().__init__(f"{phase}: {detail}")


def _unpack():
    deadline = time.monotonic() + ARTIFACT_WAIT_SECONDS
    while not os.path.isfile(ARTIFACT_PATH):
        if time.monotonic() >= deadline:
            raise PhaseError(
                "unpack",
                f"artifact not found at {ARTIFACT_PATH} after "
                f"{ARTIFACT_WAIT_SECONDS}s; is this endpoint part of an app "
                f"deployment?",
            )
        time.sleep(2)

    os.makedirs(APP_DIR, exist_ok=True)
    target = os.path.realpath(APP_DIR)
    try:
        with tarfile.open(ARTIFACT_PATH, mode="r:*") as tar:
            for member in tar.getmembers():
                dest = os.path.realpath(os.path.join(APP_DIR, member.name))
                if not dest.startswith(target + os.sep) and dest != target:
                    raise PhaseError(
                        "unpack", f"unsafe tar member path: {member.name}"
                    )
            tar.extractall(path=APP_DIR)
    except (OSError, tarfile.TarError) as exc:
        raise PhaseError("unpack", f"failed to extract artifact: {exc}")
    _log(f"artifact extracted to {APP_DIR}")


def _pip_install(packages, phase):
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", *packages],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PhaseError(phase, result.stderr[-3000:])


def _runtime_kind():
    return os.environ.get("RUNPOD_RUNTIME_KIND", "queue")


def _ensure_runtime():
    try:
        import runpod.runtimes.queue.worker  # noqa: F401

        return
    except ImportError:
        pass
    # RUNPOD_PACKAGE_SPEC allows pinning or installing from git (e.g.
    # prerelease testing); defaults to the published package
    spec = os.environ.get("RUNPOD_PACKAGE_SPEC", "runpod")
    _log(f"worker runtime not in image, installing {spec}")
    packages = [spec, "cloudpickle"]
    if _runtime_kind() == "api":
        packages.append("uvicorn>=0.30")
    _pip_install(packages, "runtime")


def _resource_entry():
    manifest_path = os.path.join(APP_DIR, MANIFEST_NAME)
    if not os.path.isfile(manifest_path):
        raise PhaseError("deps", f"manifest not found at {manifest_path}")
    with open(manifest_path) as f:
        manifest = json.load(f)
    resource_name = os.environ.get("FLASH_RESOURCE_NAME") or os.environ.get(
        "RUNPOD_RESOURCE_NAME", ""
    )
    for entry in manifest.get("resources", []):
        if entry.get("name") == resource_name:
            return entry
    raise PhaseError(
        "deps",
        f"resource '{resource_name}' not found in manifest "
        f"(has: {[r.get('name') for r in manifest.get('resources', [])]})",
    )


def _install_deps(entry):
    deps = entry.get("dependencies") or []
    if not deps:
        return
    _log(f"installing dependencies: {deps}")
    _pip_install(deps, "deps")


def _run_worker():
    module = (
        "runpod.runtimes.api.server"
        if _runtime_kind() == "api"
        else "runpod.runtimes.queue.worker"
    )
    env = dict(os.environ, RUNPOD_APP_DIR=APP_DIR)
    result = subprocess.run([sys.executable, "-m", module], env=env)
    sys.exit(result.returncode)


def _error_loop(error: PhaseError):
    """answer jobs with the bootstrap error so it surfaces in responses."""
    get_url = os.environ.get("RUNPOD_WEBHOOK_GET_JOB", "")
    post_url = os.environ.get("RUNPOD_WEBHOOK_POST_OUTPUT", "")
    api_key = os.environ.get("RUNPOD_AI_API_KEY", "")
    if not get_url or not post_url:
        _log("no job webhook env; exiting with error")
        sys.exit(1)

    payload = json.dumps(
        {
            "error": json.dumps(
                {
                    "error_type": "BootstrapError",
                    "error_message": f"worker bootstrap failed during "
                    f"'{error.phase}': {error.detail}",
                }
            )
        }
    ).encode()

    _log("starting error-reporting loop")
    while True:
        try:
            req = urllib.request.Request(
                get_url, headers={"Authorization": api_key}
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                if resp.status != 200:
                    time.sleep(2)
                    continue
                job = json.loads(resp.read() or b"{}")
            job_id = job.get("id")
            if not job_id:
                continue
            done = urllib.request.Request(
                post_url.replace("$ID", job_id),
                data=payload,
                headers={
                    "Authorization": api_key,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                method="POST",
            )
            urllib.request.urlopen(done, timeout=30)
            _log(f"reported bootstrap error for job {job_id}")
        except Exception as exc:  # noqa: BLE001 - loop must survive
            _log(f"error loop: {exc}")
            time.sleep(5)


def main():
    deployed = bool(
        os.environ.get("FLASH_RESOURCE_NAME")
        or os.environ.get("RUNPOD_RESOURCE_NAME")
    )
    try:
        if deployed:
            _unpack()
            _ensure_runtime()
            _install_deps(_resource_entry())
        else:
            # live mode (rp dev): no artifact, source ships per request
            _ensure_runtime()
    except PhaseError as exc:
        _log(f"FAILED {exc}")
        _error_loop(exc)
        return
    _run_worker()


if __name__ == "__main__":
    main()
