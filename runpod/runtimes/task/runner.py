"""single-shot task runner: the process a task pod boots into.

a minimal http server speaking the FunctionRequest/FunctionResponse
protocol. stdlib-only (cloudpickle optional, required only for the
cloudpickle serialization format) so it can bootstrap onto any python
image via an env-var payload, and be pre-baked into the task runtime
image later.

endpoints:
    GET  /ping     readiness probe (unauthenticated)
    POST /execute  run a function, block, return the response
    POST /submit   start a function in the background, return immediately
    GET  /result   status/result of the submitted job

auth: every endpoint except /ping requires
    Authorization: Bearer $RUNPOD_TASK_TOKEN

one pod runs one function; the client terminates the pod after
collecting the result.
"""

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import traceback
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("RUNPOD_TASK_PORT", "8080"))
TOKEN = os.environ.get("RUNPOD_TASK_TOKEN", "")

# single background job slot for /submit + /result
_job_lock = threading.Lock()
_job_state = {"status": "NONE", "response": None}


def _load_cloudpickle(install: bool = False):
    try:
        import cloudpickle

        return cloudpickle
    except ImportError:
        if not install:
            return None
    # bare images (custom image= without the runtime baked in) may lack
    # cloudpickle; install it on demand the same way dependencies are
    if _install(["cloudpickle"], "cloudpickle"):
        return None
    import cloudpickle

    return cloudpickle


def _install(packages, label):
    if not packages:
        return None
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", *packages],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return f"failed to install {label}: {result.stderr[-2000:]}"
    return None


_apt_updated = False


def _install_system(packages):
    """install apt packages; requires a debian-family image with root."""
    global _apt_updated
    if not packages:
        return None
    if shutil.which("apt-get") is None:
        return (
            f"system dependencies {packages} requested but apt-get is not "
            f"available in this image; use a debian-based image or bake "
            f"them in"
        )
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    if not _apt_updated:
        update = subprocess.run(
            ["apt-get", "update", "-qq"], capture_output=True, text=True, env=env
        )
        if update.returncode != 0:
            return f"apt-get update failed: {update.stderr[-2000:]}"
        _apt_updated = True
    result = subprocess.run(
        ["apt-get", "install", "-y", "-qq", "--no-install-recommends", *packages],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        return (
            f"failed to install system dependencies {packages}: "
            f"{result.stderr[-2000:]}"
        )
    return None


def _deserialize_args(request):
    fmt = request.get("serialization_format", "cloudpickle")
    args = request.get("args") or []
    kwargs = request.get("kwargs") or {}
    if fmt == "json":
        return list(args), dict(kwargs)
    cloudpickle = _load_cloudpickle(install=True)
    if cloudpickle is None:
        raise RuntimeError("cloudpickle not available for argument deserialization")
    args = [cloudpickle.loads(base64.b64decode(a)) for a in args]
    kwargs = {k: cloudpickle.loads(base64.b64decode(v)) for k, v in kwargs.items()}
    return args, kwargs


def _serialize_result(result, fmt):
    if fmt == "json":
        return {"json_result": result}
    cloudpickle = _load_cloudpickle(install=True)
    if cloudpickle is None:
        return {"json_result": result}
    return {"result": base64.b64encode(cloudpickle.dumps(result)).decode("utf-8")}


def execute_request(request):
    """run one FunctionRequest to completion, returning a response dict."""
    stdout_io = io.StringIO()
    try:
        error = _install_system(request.get("system_dependencies"))
        if error:
            return {"success": False, "error": error}
        error = _install(request.get("dependencies"), "dependencies")
        if error:
            return {"success": False, "error": error}

        function_name = request.get("function_name")
        function_code = request.get("function_code")
        if not function_name or not function_code:
            return {
                "success": False,
                "error": "function_name and function_code are required",
            }

        # exec mirrors deployed-mode module import: the code is the
        # user's full module, so __name__ is set like an import would
        # (main guards stay inert) and decorated handles resolve to
        # their wrapped functions
        namespace = {"__name__": "__runpod_live__"}
        exec(function_code, namespace)  # noqa: S102 - that is the job
        if function_name not in namespace:
            return {
                "success": False,
                "error": f"function '{function_name}' not found in provided code",
            }
        fn = namespace[function_name]
        fn = getattr(fn, "_fn", fn)

        args, kwargs = _deserialize_args(request)

        # stdout only: sys.stderr carries the runner's own request logs
        # from other threads, which must not leak into job output
        with redirect_stdout(stdout_io):
            result = fn(*args, **kwargs)
            if hasattr(result, "__await__"):
                result = _run_awaitable(result)

        response = {"success": True, "stdout": stdout_io.getvalue()}
        response.update(
            _serialize_result(
                result, request.get("serialization_format", "cloudpickle")
            )
        )
        return response
    except Exception:  # noqa: BLE001 - all failures go over the wire
        return {
            "success": False,
            "error": traceback.format_exc(),
            "stdout": stdout_io.getvalue(),
        }


def _run_awaitable(awaitable):
    """drive an awaitable to completion from sync code.

    the task runner runs this on plain threads, but the queue worker's
    live mode calls execute_request from inside the serverless event
    loop, where asyncio.run would raise; a private loop on a helper
    thread covers both.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await(awaitable))

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _await(awaitable)).result()


async def _await(awaitable):
    return await awaitable


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        header = self.headers.get("Authorization", "")
        return TOKEN and header == f"Bearer {TOKEN}"

    def _read_request(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler api
        if self.path == "/ping":
            self._send(200, {"ready": True})
            return
        if self.path == "/result":
            if not self._authed():
                self._send(401, {"error": "unauthorized"})
                return
            with _job_lock:
                self._send(
                    200,
                    {
                        "status": _job_state["status"],
                        "response": _job_state["response"],
                    },
                )
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler api
        if not self._authed():
            self._send(401, {"error": "unauthorized"})
            return
        if self.path == "/execute":
            self._send(200, execute_request(self._read_request()))
            return
        if self.path == "/submit":
            request = self._read_request()
            with _job_lock:
                if _job_state["status"] == "RUNNING":
                    self._send(409, {"error": "a job is already running"})
                    return
                _job_state["status"] = "RUNNING"
                _job_state["response"] = None

            def run():
                response = execute_request(request)
                with _job_lock:
                    _job_state["status"] = "DONE"
                    _job_state["response"] = response

            threading.Thread(target=run, daemon=True).start()
            self._send(200, {"status": "RUNNING"})
            return
        self._send(404, {"error": "not found"})

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        sys.stderr.write(f"[task-runner] {format % args}\n")


def main() -> None:
    if not TOKEN:
        sys.stderr.write("[task-runner] RUNPOD_TASK_TOKEN not set, exiting\n")
        sys.exit(1)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    sys.stderr.write(f"[task-runner] listening on :{PORT}\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
