"""single-shot task runner: the process a task pod boots into.

a minimal http server speaking the FunctionRequest/FunctionResponse
protocol, driving the shared execution engine in
runpod.runtimes.executor.

endpoints:
    GET  /ping     readiness probe (unauthenticated)
    POST /execute  run a function, block, return the response
    POST /submit   start a function in the background, return immediately
    GET  /result   status/result of the submitted job

auth: every endpoint except /ping requires
    Authorization: Bearer $RUNPOD_TASK_TOKEN

one pod runs one function; the client terminates the pod after
collecting the result.

ships to bare images as a single file: the sdk concatenates the
executor source above this module, so the import below is satisfied
either way.
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from runpod.runtimes.executor import execute_request
except ImportError:
    # single-file bootstrap: the executor source is concatenated above
    # this module and its names are already in scope
    pass

PORT = int(os.environ.get("RUNPOD_TASK_PORT", "8080"))
TOKEN = os.environ.get("RUNPOD_TASK_TOKEN", "")

# watchdog: self-terminate when the client is clearly gone. RUNNING
# jobs are never killed (terminateAfter is the runaway backstop);
# NONE means the client died before submitting, DONE means the result
# sat uncollected. normal flows poll every ~2s, so these never fire
# for a live client.
IDLE_TIMEOUT = float(os.environ.get("RUNPOD_TASK_IDLE_TIMEOUT", "600"))
WATCHDOG_INTERVAL = 15.0

# single background job slot for /submit + /result
_job_lock = threading.Lock()
_job_state = {"status": "NONE", "response": None}
_last_contact = {"ts": None}  # set at server start
# inline /execute requests in flight; the watchdog must not terminate
# the pod while one runs (long executes outlive the idle timeout)
_inline_executions = {"count": 0}


def _touch_contact():
    import time

    _last_contact["ts"] = time.time()


def _should_self_terminate(status, last_contact, now, idle_timeout, inline=0):
    """the watchdog decision: kill only provably-abandoned pods."""
    if status == "RUNNING" or inline > 0:
        return False
    if last_contact is None:
        return False
    return (now - last_contact) > idle_timeout


def _terminate_self():
    """terminate this pod via the injected pod-scoped api key.

    every pod carries a RUNPOD_API_KEY scoped to itself; podTerminate
    with it removes the pod entirely. exiting the process is the
    fallback (stops the workload; terminateAfter finishes the job).
    """
    import json as _json
    import urllib.request

    pod_id = os.environ.get("RUNPOD_POD_ID")
    api_key = os.environ.get("RUNPOD_API_KEY")
    if pod_id and api_key:
        try:
            api_base = os.environ.get(
                "RUNPOD_API_BASE_URL", "https://api.runpod.io"
            )
            payload = _json.dumps(
                {
                    "query": (
                        "mutation podTerminate($input: PodTerminateInput!) "
                        "{ podTerminate(input: $input) }"
                    ),
                    "variables": {"input": {"podId": pod_id}},
                }
            ).encode()
            request = urllib.request.Request(
                f"{api_base}/graphql",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
            )
            urllib.request.urlopen(request, timeout=30)  # noqa: S310
            sys.stderr.write("[task-runner] self-terminated (abandoned)\n")
        except Exception:  # noqa: BLE001 - fall through to process exit
            pass
    os._exit(0)


def _watchdog():
    import time

    while True:
        time.sleep(WATCHDOG_INTERVAL)
        if _should_self_terminate(
            _job_state["status"],
            _last_contact["ts"],
            time.time(),
            IDLE_TIMEOUT,
            inline=_inline_executions["count"],
        ):
            _terminate_self()


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
        authed = TOKEN and header == f"Bearer {TOKEN}"
        if authed:
            # any authenticated contact proves the client is alive
            _touch_contact()
        return authed

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
            with _job_lock:
                _inline_executions["count"] += 1
            try:
                self._send(200, execute_request(self._read_request()))
            finally:
                with _job_lock:
                    _inline_executions["count"] -= 1
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
        # request logging is noise in container logs (dev sessions
        # stream them as the function's output)
        pass


def main() -> None:
    if not TOKEN:
        sys.stderr.write("[task-runner] RUNPOD_TASK_TOKEN not set, exiting\n")
        sys.exit(1)
    _touch_contact()
    threading.Thread(target=_watchdog, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
