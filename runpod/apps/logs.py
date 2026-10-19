"""pod logs with rest streams and host-api snapshots.

single-source streams use rest pod or serverless-worker routes. combined
streams and finite snapshots use the host api, which supports both.
"""

import json
import os
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

import aiohttp

from ..api.ctl_commands import _path_segment
from ..api.rest import _build_url

HAPI_BASE = os.environ.get("RUNPOD_HAPI_URL", "https://hapi.runpod.net")

STREAM_TIMEOUT_SECONDS = 3600.0


def _headers() -> Dict[str, str]:
    from .utils.network import api_key

    return {"Authorization": f"Bearer {api_key()}"}


async def pod_logs(
    pod_id: str,
    *,
    log_type: str = "all",
    timeout: float = 30.0,
) -> Dict[str, List[str]]:
    """snapshot of a pod's logs: {"container": [...], "system": [...]}."""
    url = f"{HAPI_BASE}/v1/pod/{pod_id}/logs"
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.get(
            url, params={"type": log_type}, headers=_headers()
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
    return {k: v or [] for k, v in data.items()}


async def stream_pod_logs(
    pod_id: str,
    *,
    endpoint_id: Optional[str] = None,
    log_type: Optional[str] = "all",
    tail: int = 100,
    since: Optional[str] = None,
    last_event_id: Optional[str] = None,
    on_event_id: Optional[Callable[[str], None]] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """follow a pod's logs as they arrive.

    yields {"source": "container"|"system", "line": str, "ts": str}
    parsed from the sse stream. pass endpoint_id for serverless workers.
    log_type=None selects both sources through rest. last_event_id resumes
    a stream; on_event_id receives the cursor of each delivered event.
    """
    params: Dict[str, Any] = {"tail": str(tail)}
    if log_type == "all":
        url = f"{HAPI_BASE}/v1/pod/{_path_segment(pod_id)}/logs"
        params.update(stream="true", type=log_type)
    else:
        if endpoint_id is None:
            path = f"/v2/pods/{_path_segment(pod_id)}/logs"
        else:
            path = (
                f"/v2/serverless/{_path_segment(endpoint_id)}"
                f"/workers/{_path_segment(pod_id)}/logs"
            )
        url = _build_url(path)
        if log_type:
            params["source"] = log_type
    if since:
        params["since"] = since
    headers = _headers()
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id

    client_timeout = aiohttp.ClientTimeout(total=STREAM_TIMEOUT_SECONDS, sock_read=60)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.get(url, params=params, headers=headers) as resp:
            resp.raise_for_status()
            event_id = None
            async for raw in resp.content:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("id:"):
                    event_id = line[3:].strip()
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[len("data:") :].strip())
                except json.JSONDecodeError:
                    continue
                if event_id is not None and on_event_id is not None:
                    on_event_id(event_id)
                yield event


def tail_summary(logs: Dict[str, List[str]], lines: int = 20) -> str:
    """human-readable tail of a log snapshot, for error messages."""
    parts = []
    for source in ("system", "container"):
        entries = logs.get(source) or []
        if entries:
            parts.append(f"--- {source} (last {min(lines, len(entries))}) ---")
            parts.extend(entries[-lines:])
    return "\n".join(parts) if parts else "(no logs available)"
