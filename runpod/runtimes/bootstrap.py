"""stdlib-only entrypoint for app workers.

cold starts do no dependency resolution: the build artifact ships a
vendored environment (env/ inside the artifact) containing the runpod
runtime and every python dependency, resolved at deploy time for the
worker platform. this entrypoint only has to make that environment
visible and exec the worker.

deployed mode (FLASH_RESOURCE_NAME set):
  locate   find the app tree: a pre-unpacked directory if the host
           provides one, else extract the artifact tarball once
  attach   put {app} and {app}/env at the front of PYTHONPATH
  verify   packages the build excluded by size (torch and friends)
           must come from the image; genuinely absent ones are
           installed as a fallback, loudly
  system   apt packages from the resource's system_dependencies;
           these cannot ride in the artifact, so this is the one
           install that legitimately remains at cold start
  serve    exec the worker runtime from the vendored environment

live mode (no resource name, `rp dev`):
  no artifact exists; source arrives per request. the runtime images
  bake the runpod package in, custom images get it installed here.
  serve    exec the worker runtime

if any phase fails the worker does not crash-loop silently: a minimal
job-take loop answers every job with a structured BootstrapError so
the failure surfaces in job responses (queue endpoints), or an http
server answers every request with the error (api endpoints).

stdlib only: this file runs on the image's python before any
environment exists. it is baked into the runtime images as CMD and
injected into custom images via RUNPOD_BOOTSTRAP_B64 + dockerArgs.
"""

import importlib.util
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
# a host-provided, already-unpacked app tree; when present, unpacking
# is skipped entirely
PREBUILT_APP_DIR = os.environ.get("RUNPOD_PREBUILT_APP_DIR", "")
APP_DIR = os.environ.get("RUNPOD_APP_DIR", "/app")
ENV_SUBDIR = "env"
ARTIFACT_WAIT_SECONDS = int(os.environ.get("RUNPOD_ARTIFACT_WAIT", "300"))
MANIFEST_NAME = "runpod_manifest.json"
UNPACK_MARKER = ".rp-unpacked"

WORKER_MODULES = {
    "queue": "runpod.runtimes.queue.worker",
    "api": "runpod.runtimes.api.server",
}


def _log(message):
    sys.stderr.write(f"[bootstrap] {message}\n")
    sys.stderr.flush()


class PhaseError(Exception):
    def __init__(self, phase, detail):
        self.phase = phase
        self.detail = detail
        super().__init__(f"{phase}: {detail}")


def _runtime_kind():
    return os.environ.get("RUNPOD_RUNTIME_KIND", "queue")


def _resource_name():
    return os.environ.get("FLASH_RESOURCE_NAME") or os.environ.get(
        "RUNPOD_RESOURCE_NAME", ""
    )


# ---------------------------------------------------------------- locate


def _locate():
    """return the app tree root, unpacking the artifact if needed."""
    if PREBUILT_APP_DIR and os.path.isdir(PREBUILT_APP_DIR):
        _log(f"using pre-unpacked app tree at {PREBUILT_APP_DIR}")
        return PREBUILT_APP_DIR

    marker = os.path.join(APP_DIR, UNPACK_MARKER)
    if os.path.isfile(marker):
        _log(f"app tree already unpacked at {APP_DIR}")
        return APP_DIR

    deadline = time.monotonic() + ARTIFACT_WAIT_SECONDS
    while not os.path.isfile(ARTIFACT_PATH):
        if time.monotonic() >= deadline:
            raise PhaseError(
                "locate",
                f"artifact not found at {ARTIFACT_PATH} after "
                f"{ARTIFACT_WAIT_SECONDS}s; is this endpoint part of an "
                f"app deployment?",
            )
        time.sleep(2)

    os.makedirs(APP_DIR, exist_ok=True)
    target = os.path.realpath(APP_DIR)
    started = time.monotonic()
    try:
        with tarfile.open(ARTIFACT_PATH, mode="r:*") as tar:
            for member in tar.getmembers():
                dest = os.path.realpath(os.path.join(APP_DIR, member.name))
                if not dest.startswith(target + os.sep) and dest != target:
                    raise PhaseError(
                        "locate", f"unsafe tar member path: {member.name}"
                    )
            tar.extractall(path=APP_DIR)
    except (OSError, tarfile.TarError) as exc:
        raise PhaseError("locate", f"failed to extract artifact: {exc}")

    with open(marker, "w") as f:
        f.write(str(time.time()))
    _log(
        f"artifact extracted to {APP_DIR} "
        f"in {time.monotonic() - started:.1f}s"
    )
    return APP_DIR


# ---------------------------------------------------------------- attach


def _attach(app_dir):
    """paths that must lead sys.path in the worker process.

    the vendored env comes first so the artifact's runpod (matched to
    the client that built it) wins over anything baked into the image.
    """
    env_dir = os.path.join(app_dir, ENV_SUBDIR)
    paths = [app_dir]
    if os.path.isdir(env_dir):
        paths.insert(0, env_dir)
    else:
        _log(
            f"artifact has no vendored env at {env_dir}; "
            f"falling back to image packages"
        )
    return paths


def _manifest(app_dir):
    path = os.path.join(app_dir, MANIFEST_NAME)
    if not os.path.isfile(path):
        raise PhaseError("attach", f"manifest not found at {path}")
    with open(path) as f:
        return json.load(f)


def _worker_importable(paths):
    """can the worker module be imported with these paths leading?"""
    module = WORKER_MODULES[_runtime_kind()]
    probe = (
        "import sys; sys.path[:0] = sys.argv[1:]; "
        f"import importlib; importlib.import_module({module!r})"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe, *paths],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0, result.stderr[-2000:]


# ---------------------------------------------------------------- verify


def _pip_install(packages, phase):
    env = dict(os.environ)
    # tarball specs have no .git for setuptools-scm version inference
    env.setdefault("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_RUNPOD", "0.0.0.dev0")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "--upgrade", *packages],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise PhaseError(phase, result.stderr[-3000:])


def _resource_entry(manifest):
    name = _resource_name()
    for entry in manifest.get("resources", []):
        if entry.get("name") == name:
            return entry
    return {}


def _install_system(manifest):
    """apt packages cannot be vendored; install this resource's
    system_dependencies at cold start."""
    packages = _resource_entry(manifest).get("systemDependencies") or []
    if not packages:
        return
    import shutil

    if shutil.which("apt-get") is None:
        raise PhaseError(
            "system",
            f"system dependencies {packages} requested but apt-get is "
            f"not available in this image; use a debian-based image or "
            f"bake them in",
        )
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    _log(f"installing system dependencies: {packages}")
    update = subprocess.run(
        ["apt-get", "update", "-qq"], capture_output=True, text=True, env=env
    )
    if update.returncode != 0:
        raise PhaseError("system", f"apt-get update failed: {update.stderr[-2000:]}")
    result = subprocess.run(
        ["apt-get", "install", "-y", "-qq", "--no-install-recommends", *packages],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise PhaseError(
            "system",
            f"failed to install system dependencies {packages}: "
            f"{result.stderr[-2000:]}",
        )


def _verify_excluded(manifest):
    """packages the build excluded must come from the image.

    a miss means the image was swapped for one without them; install as
    a fallback so the worker still comes up, but say so loudly because
    it re-adds the cold-start cost the exclusion existed to avoid.
    """
    excluded = manifest.get("excludedPackages") or []
    missing = [
        name
        for name in excluded
        if importlib.util.find_spec(name.replace("-", "_")) is None
    ]
    if not missing:
        return
    _log(
        f"WARNING: excluded packages {missing} are not in this image; "
        f"installing at cold start. use an image that provides them "
        f"(e.g. runpod/pytorch) to avoid this cost."
    )
    _pip_install(missing, "verify")


# ---------------------------------------------------------------- serve


def _serve(paths):
    """exec the worker runtime with the environment paths leading."""
    module = WORKER_MODULES[_runtime_kind()]
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        paths + ([existing] if existing else [])
    )
    if paths:
        env["RUNPOD_APP_DIR"] = paths[-1]
    os.execve(
        sys.executable,
        [sys.executable, "-m", module],
        env,
    )


# ------------------------------------------------------------- live mode


def _ensure_runtime_installed():
    """live mode on a custom image: the runtime must be importable.

    baked runtime images make this a no-op. --upgrade matters: the
    image may carry an older runpod without the runtimes modules, and
    a plain install would no-op against it.
    """
    ok, _ = _worker_importable([])
    if ok:
        return
    spec = os.environ.get("RUNPOD_PACKAGE_SPEC", "runpod")
    _log(f"worker runtime not in image, installing {spec}")
    packages = [spec, "cloudpickle"]
    if _runtime_kind() == "api":
        packages.append("uvicorn>=0.30")
    _pip_install(packages, "runtime")
    ok, err = _worker_importable([])
    if not ok:
        raise PhaseError(
            "runtime",
            f"installed {spec} but the worker runtime is still not "
            f"importable: {err}. set RUNPOD_PACKAGE_SPEC to a version "
            f"that includes runpod.runtimes.",
        )


# ----------------------------------------------------------- error surface


def _error_payload(error):
    return {
        "error_type": "BootstrapError",
        "error_message": (
            f"worker bootstrap failed during '{error.phase}': {error.detail}"
        ),
    }


def _queue_error_loop(error):
    """answer queue jobs with the bootstrap error."""
    get_url = os.environ.get("RUNPOD_WEBHOOK_GET_JOB", "")
    post_url = os.environ.get("RUNPOD_WEBHOOK_POST_OUTPUT", "")
    api_key = os.environ.get("RUNPOD_AI_API_KEY", "")
    if not get_url or not post_url:
        _log("no job webhook env; exiting with error")
        sys.exit(1)

    payload = json.dumps({"error": json.dumps(_error_payload(error))}).encode()

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


def _api_error_server(error):
    """answer every http request with the bootstrap error."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    body = json.dumps(_error_payload(error)).encode()
    port = int(os.environ.get("PORT", "80"))

    class Handler(BaseHTTPRequestHandler):
        def _respond(self):
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _respond

        def log_message(self, *args):
            pass

    _log(f"starting error-reporting http server on :{port}")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


def _report_error(error):
    _log(f"FAILED {error}")
    if _runtime_kind() == "api":
        _api_error_server(error)
    else:
        _queue_error_loop(error)


# ------------------------------------------------------------------ main


def main():
    try:
        if _resource_name():
            app_dir = _locate()
            paths = _attach(app_dir)
            manifest = _manifest(app_dir)
            _verify_excluded(manifest)
            _install_system(manifest)
            ok, err = _worker_importable(paths)
            if not ok:
                raise PhaseError(
                    "attach",
                    f"worker runtime not importable from the artifact "
                    f"env or the image: {err}",
                )
            _serve(paths)
        else:
            _ensure_runtime_installed()
            _serve([])
    except PhaseError as exc:
        _report_error(exc)


if __name__ == "__main__":
    main()
