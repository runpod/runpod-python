"""request-time worker observability for dev sessions.

while a dev call is in flight, a WorkerMonitor watches the endpoint's
worker metrics for state transitions (initializing, throttled, ready)
and, once the job is assigned a worker, follows that worker's container
logs over the hapi sse stream. everything surfaces through a duck-typed
event sink; missing handlers are silently skipped.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .utils.events import emit

log = logging.getLogger(__name__)

METRICS_POLL_INTERVAL = 2.0

# worker states worth reporting, in display order
_TRACKED_STATES = ("initializing", "ready", "running", "throttled", "unhealthy")

# leading iso timestamp on hapi log lines
_TS_PREFIX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\s*"
)


def _worker_frame(line: str) -> Optional[Dict[str, Any]]:
    """parse the serverless sdk's structured json log lines."""
    if not line.startswith("{"):
        return None
    try:
        frame = json.loads(line)
    except json.JSONDecodeError:
        return None
    if isinstance(frame, dict) and "message" in frame and "level" in frame:
        return frame
    return None


def _filter_line(line: str) -> Optional[str]:
    """what to show from a raw container log line.

    the serverless sdk's own frames (fitness checks, queue counts,
    started/finished) are runtime noise; user prints pass through
    verbatim, and sdk error/warn frames surface as their message.
    """
    if line.startswith("--- Starting Serverless Worker"):
        return None
    frame = _worker_frame(line)
    if frame is None:
        return line
    if frame.get("level", "").upper() in ("ERROR", "WARN", "WARNING"):
        return str(frame.get("message", ""))
    return None


class PodLogStream:
    """follows one pod's container logs and emits worker_log events.

    shared by live endpoint workers and task pods: attach() starts the
    follow (retrying aggressively while the gateway warms up to a fresh
    pod), stop() cancels it and falls back to a snapshot when the
    stream never yielded a line (jobs shorter than the attach window).
    lines dedup across reconnects and sdk runtime frames are filtered.
    """

    def __init__(self, pod_id: str, resource_name: str, events: object):
        self.pod_id = pod_id
        self.name = resource_name
        self.events = events
        self._task: Optional[asyncio.Task] = None
        self._since = datetime.now(timezone.utc).isoformat()
        self._lines_emitted = 0
        # (ts, line) pairs already shown; reconnect backfill overlaps
        # the previous window and must not replay lines
        self._seen: set = set()

    def attach(self) -> None:
        if self._task is None:
            self._task = asyncio.ensure_future(self._follow())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        # the stream often cannot attach within a short job's lifetime
        # (fresh pods 403 on the logs endpoint until the gateway can
        # resolve them); a final snapshot recovers the output
        if not self._lines_emitted:
            await self._snapshot()

    def _emit_line(self, key: tuple, line: str) -> None:
        if key in self._seen:
            return
        self._seen.add(key)
        shown = _filter_line(line)
        if shown:
            emit(self.events, "worker_log", self.name, shown)

    async def _snapshot(self) -> None:
        from .logs import pod_logs

        try:
            logs = await pod_logs(self.pod_id, log_type="container")
        except Exception:  # noqa: BLE001 - observability is best-effort
            log.debug("log snapshot for %s failed", self.pod_id, exc_info=True)
            return
        for raw in logs.get("container") or []:
            if _line_before(raw, self._since):
                continue
            line = _TS_PREFIX.sub("", raw.rstrip())
            if line:
                self._emit_line((raw.rstrip(), line), line)

    async def _follow(self) -> None:
        from .logs import stream_pod_logs

        attempt = 0
        while True:
            streamed = False
            try:
                # backfill (tail) + since: lines printed between request
                # start and the attach finally succeeding (fresh pods
                # 403 until the gateway can resolve them) are recovered
                # from the backfill instead of dropped
                async for event in stream_pod_logs(
                    self.pod_id,
                    log_type="container",
                    tail=1000,
                    since=self._since,
                ):
                    streamed = True
                    attempt = 0
                    raw = (event.get("line") or "").rstrip()
                    line = _TS_PREFIX.sub("", raw)
                    if line:
                        self._lines_emitted += 1
                        self._emit_line((event.get("ts") or raw, line), line)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - observability is best-effort
                log.debug(
                    "log stream attach for %s failed (attempt %d)",
                    self.pod_id,
                    attempt + 1,
                    exc_info=True,
                )
            if streamed:
                # the server closed a healthy stream (1h cap or worker
                # teardown); resume immediately from where we left off
                self._since = datetime.now(timezone.utc).isoformat()
                continue
            attempt += 1
            # attach latency is the product: retry aggressively while
            # the gateway warms up to the fresh pod (sub-second polls),
            # then ease off if it stays unreachable
            if attempt <= 20:
                await asyncio.sleep(0.5)
            else:
                await asyncio.sleep(min(attempt - 20, 15))


class WorkerMonitor:
    """observes one in-flight request against a live endpoint.

    start() launches a metrics poller; on_status(payload) should be fed
    every job-status payload so the assigned worker's log stream can
    attach as soon as a workerId appears. stop() tears everything down.
    """

    def __init__(
        self,
        endpoint_id: str,
        resource_name: str,
        events: object,
        metrics_key: Optional[str] = None,
    ):
        self.endpoint_id = endpoint_id
        self.name = resource_name
        self.events = events
        # /metrics is served on the data plane behind the endpoint's own
        # ai key; the user api key is rejected there
        self.metrics_key = metrics_key
        self._tasks: List[asyncio.Task] = []
        self._streams: Dict[str, PodLogStream] = {}
        self._last_counts: Optional[Dict[str, int]] = None

    async def start(self) -> None:
        if self.metrics_key:
            self._tasks.append(asyncio.ensure_future(self._poll_metrics()))

    def on_status(self, data: Dict[str, Any]) -> None:
        """inspect a job-status payload for the assigned worker."""
        worker_id = data.get("workerId")
        if worker_id and worker_id not in self._streams:
            emit(self.events, "worker_ready", self.name, str(worker_id))
            stream = PodLogStream(str(worker_id), self.name, self.events)
            stream.attach()
            self._streams[str(worker_id)] = stream

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for stream in self._streams.values():
            await stream.stop()

    async def _poll_metrics(self) -> None:
        from .targets import _endpoint_url_base, _get_json

        url = f"{_endpoint_url_base()}/{self.endpoint_id}/metrics"
        headers = {"Authorization": f"Bearer {self.metrics_key}"}
        while True:
            try:
                data = await _get_json(url, headers, 10.0)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - observability is best-effort
                await asyncio.sleep(METRICS_POLL_INTERVAL)
                continue
            self._report_counts(data)
            await asyncio.sleep(METRICS_POLL_INTERVAL)

    def _report_counts(self, data: Dict[str, Any]) -> None:
        # once a worker picked the job up, pool churn is noise
        if self._streams:
            return
        workers = data.get("workers")
        if not isinstance(workers, dict):
            return
        counts = {
            state: workers.get(state, 0) or 0 for state in _TRACKED_STATES
        }
        if counts == self._last_counts:
            return
        first = self._last_counts is None
        self._last_counts = counts
        # the first snapshot is only interesting when workers are still
        # coming up (or wedged); a steady ready/running pool is implied
        if first and not (
            counts["initializing"] or counts["throttled"] or counts["unhealthy"]
        ):
            return
        emit(self.events, "worker_status", self.name, counts)


def _line_before(raw: str, since_iso: str) -> bool:
    """true when the log line's leading timestamp predates since."""
    match = _TS_PREFIX.match(raw)
    if not match:
        return False
    ts = match.group(0).strip().replace("Z", "+00:00")
    # docker timestamps carry nanoseconds; fromisoformat caps at micro
    ts = re.sub(r"(\.\d{6})\d+", r"\1", ts)
    try:
        line_ts = datetime.fromisoformat(ts)
        since = datetime.fromisoformat(since_iso)
    except ValueError:
        return False
    return line_ts <= since


def format_worker_counts(counts: Dict[str, int]) -> str:
    """human summary like '1 initializing, 2 ready'."""
    parts = [
        f"{count} {state}"
        for state, count in counts.items()
        if count
    ]
    return ", ".join(parts) if parts else "no workers"
