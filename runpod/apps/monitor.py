"""request-time worker observability for dev sessions.

while a dev call is in flight, a WorkerMonitor watches the endpoint's
worker metrics for state transitions (initializing, throttled, ready)
and, once the job is assigned a worker, follows that worker's container
logs over the hapi sse stream. everything surfaces through a duck-typed
event sink; missing handlers are silently skipped.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

METRICS_POLL_INTERVAL = 2.0

# worker states worth reporting, in display order
_TRACKED_STATES = ("initializing", "ready", "running", "throttled", "unhealthy")

# leading iso timestamp on hapi log lines
_TS_PREFIX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\s*"
)


def emit(sink: Optional[object], event: str, *args: Any) -> None:
    """invoke a handler on the sink if it exists."""
    handler = getattr(sink, event, None)
    if handler is not None:
        try:
            handler(*args)
        except Exception:  # noqa: BLE001 - rendering must never break calls
            log.debug("event sink %s failed", event, exc_info=True)


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
        self._streamed_workers: set = set()
        self._last_counts: Optional[Dict[str, int]] = None
        self._since = datetime.now(timezone.utc).isoformat()

    async def start(self) -> None:
        if self.metrics_key:
            self._tasks.append(asyncio.ensure_future(self._poll_metrics()))

    def on_status(self, data: Dict[str, Any]) -> None:
        """inspect a job-status payload for the assigned worker."""
        worker_id = data.get("workerId")
        if worker_id and worker_id not in self._streamed_workers:
            self._streamed_workers.add(worker_id)
            emit(self.events, "worker_ready", self.name, str(worker_id))
            self._tasks.append(
                asyncio.ensure_future(self._stream_logs(str(worker_id)))
            )

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

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

    async def _stream_logs(self, worker_id: str) -> None:
        from .logs import stream_pod_logs

        try:
            async for event in stream_pod_logs(
                worker_id,
                log_type="container",
                tail=0,
                since=self._since,
            ):
                line = _TS_PREFIX.sub("", (event.get("line") or "").rstrip())
                if line:
                    emit(self.events, "worker_log", self.name, line)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - observability is best-effort
            log.debug("log stream for %s ended", worker_id, exc_info=True)


def format_worker_counts(counts: Dict[str, int]) -> str:
    """human summary like '1 initializing · 2 ready'."""
    parts = [
        f"{count} {state}"
        for state, count in counts.items()
        if count
    ]
    return " · ".join(parts) if parts else "no workers"
