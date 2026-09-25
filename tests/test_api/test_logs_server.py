"""Log readers against a local fake of the Runpod REST v2 log endpoints.

These run over real sockets so the requests/urllib3 streaming behavior (chunked
reads, read timeouts, early close) is exercised, not mocked.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

import runpod
from runpod.api import ctl_commands
from runpod.error import QueryError

EVENTS = [
    {"ts": f"2026-06-01T12:00:0{i}Z", "source": "container", "line": f"line {i} é"}
    for i in range(6)
]


def _frame(index):
    return f"id: {index}\ndata: {json.dumps(EVENTS[index])}\n\n".encode()


class FakeRunpod(BaseHTTPRequestHandler):
    """Serves a log stream whose behavior is picked by the resource ID."""

    protocol_version = "HTTP/1.1"
    requests_seen = []
    rate_limited = set()

    def log_message(self, *_args):
        pass

    def _chunk(self, data):
        self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
        self.wfile.flush()

    def _json(self, status, payload, headers=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/problem+json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _start_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _end_stream(self):
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()
        self.close_connection = True

    def do_GET(self):  # pylint: disable=invalid-name
        url = urlsplit(self.path)
        FakeRunpod.requests_seen.append(
            {
                "path": url.path,
                "query": parse_qs(url.query),
                "last_event_id": self.headers.get("Last-Event-ID"),
                "accept": self.headers.get("Accept"),
            }
        )
        if url.path == "/v2/serverless/ep/workers":
            self._json(200, {"workers": [{"id": "w1"}], "summary": {"RUNNING": 1}})
            return

        mode = url.path.split("/")[-2]
        resume_from = int(self.headers.get("Last-Event-ID", "-1")) + 1
        if mode == "missing":
            self._json(404, {"title": "Not Found", "status": 404, "detail": "pod not found"})
            return
        if mode == "ratelimit" and resume_from > 0 and mode not in self.rate_limited:
            FakeRunpod.rate_limited.add(mode)
            self._json(429, {"detail": "rate limited"}, {"Retry-After": "0"})
            return

        self._start_stream()
        try:
            if mode in ("resume", "ratelimit"):
                # Two events per connection, then close: the client must resume.
                for index in range(resume_from, min(resume_from + 2, len(EVENTS))):
                    self._chunk(_frame(index))
                self._end_stream()
                return
            for index in range(3):
                self._chunk(_frame(index))
            if mode == "chatty":
                index = 3
                while True:
                    self._chunk(_frame(index % len(EVENTS)))
                    index += 1
                    time.sleep(0.02)
            if mode == "partial":
                self._chunk(b'id: 9\ndata: {"line": "cut')
            time.sleep(10)
        except (BrokenPipeError, ConnectionResetError):
            pass


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


@pytest.fixture(name="server", scope="module")
def fixture_server():
    server = QuietServer(("127.0.0.1", 0), FakeRunpod)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(autouse=True)
def fixture_api(server, monkeypatch):
    monkeypatch.setenv("RUNPOD_API_BASE_URL", server)
    monkeypatch.setattr(runpod, "api_key", "key")
    monkeypatch.setattr(ctl_commands, "LOG_RECONNECT_DELAY", 0.01)
    FakeRunpod.requests_seen.clear()
    FakeRunpod.rate_limited.clear()


def _lines(logs):
    return [entry["line"] for entry in logs]


def test_snapshot_returns_backfill_when_stream_goes_idle():
    started = time.monotonic()
    logs = runpod.get_pod_logs("idle", tail=3, source="container", max_wait=0.5)

    assert time.monotonic() - started < 2
    assert logs[0] == {"id": "0", **EVENTS[0]}
    assert _lines(logs) == ["line 0 é", "line 1 é", "line 2 é"]
    assert FakeRunpod.requests_seen == [
        {
            "path": "/v2/pods/idle/logs",
            "query": {"tail": ["3"], "source": ["container"]},
            "last_event_id": None,
            "accept": "text/event-stream",
        }
    ]


def test_snapshot_stops_at_deadline_on_busy_stream():
    started = time.monotonic()
    logs = runpod.get_pod_logs("chatty", max_wait=0.5)

    assert time.monotonic() - started < 1.5
    assert len(logs) > 3


def test_snapshot_discards_event_cut_mid_frame():
    assert _lines(runpod.get_pod_logs("partial", max_wait=0.5)) == [
        "line 0 é",
        "line 1 é",
        "line 2 é",
    ]


def test_snapshot_keeps_newest_lines_past_byte_cap():
    logs = runpod.get_pod_logs("idle", max_wait=0.5, max_bytes=len("line 0 é") * 2)

    assert _lines(logs) == ["line 1 é", "line 2 é"]


def test_missing_pod_raises_query_error():
    with pytest.raises(QueryError, match="pod not found") as raised:
        runpod.get_pod_logs("missing", max_wait=0.5)

    assert raised.value.status_code == 404
    with pytest.raises(QueryError):
        next(runpod.iter_pod_logs("missing"))


def test_follow_resumes_from_last_event_id_without_gaps_or_repeats():
    logs = runpod.iter_pod_logs("resume")
    entries = [next(logs) for _ in range(len(EVENTS))]
    logs.close()

    assert [entry["id"] for entry in entries] == ["0", "1", "2", "3", "4", "5"]
    assert [seen["last_event_id"] for seen in FakeRunpod.requests_seen[:3]] == [
        None,
        "1",
        "3",
    ]


def test_follow_waits_out_rate_limit_on_reconnect():
    logs = runpod.iter_pod_logs("ratelimit")
    entries = [next(logs) for _ in range(4)]
    logs.close()

    assert [entry["id"] for entry in entries] == ["0", "1", "2", "3"]
    assert [seen["last_event_id"] for seen in FakeRunpod.requests_seen[:3]] == [
        None,
        "1",
        "1",
    ]


def test_follow_stops_at_max_wait():
    started = time.monotonic()
    logs = list(runpod.iter_pod_logs("idle", max_wait=0.5))

    assert time.monotonic() - started < 2
    assert _lines(logs)[:3] == ["line 0 é", "line 1 é", "line 2 é"]
    assert all(entry["id"] in {"0", "1", "2"} for entry in logs[3:])


def test_worker_logs_and_worker_listing():
    workers = runpod.get_endpoint_workers("ep")
    logs = runpod.get_endpoint_worker_logs("ep", workers[0]["id"], max_wait=0.5)

    assert workers == [{"id": "w1"}]
    assert len(logs) == 3
    assert FakeRunpod.requests_seen[-1]["path"] == "/v2/serverless/ep/workers/w1/logs"
