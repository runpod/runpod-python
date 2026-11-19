"""pod logs with rest streams and host-api snapshots.

single-source streams use rest pod or serverless-worker routes. combined
streams and finite snapshots use the host api, which supports both.
"""

import asyncio
import json
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

import aiohttp

from ..api.http import HTTPClient
from ..api.routes import Route, host_api_base_url
from ..user_agent import APPS_USER_AGENT
from .utils.http import raise_for_status

STREAM_TIMEOUT_SECONDS = 3600.0


def _headers() -> Dict[str, str]:
    from .utils.network import api_key

    return {"Authorization": f"Bearer {api_key()}", "User-Agent": APPS_USER_AGENT}


async def pod_logs(
    pod_id: str,
    *,
    log_type: str = "all",
    timeout: float = 30.0,
) -> Dict[str, List[str]]:
    """snapshot of a pod's logs: {"container": [...], "system": [...]}."""
    async with HTTPClient(base_url=host_api_base_url()).stream(
        Route("GET", "/v1/pod/{pod_id}/logs", pod_id=pod_id),
        params={"type": log_type},
        headers=_headers(),
        timeout=timeout,
    ) as resp:
        await raise_for_status(resp)
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
    idle_timeout: Optional[float] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """follow a pod's logs as they arrive.

    yields {"source": "container"|"system", "line": str, "ts": str}
    parsed from the sse stream. pass endpoint_id for serverless workers.
    log_type=None selects both sources through rest. last_event_id resumes
    a stream; on_event_id receives the cursor of each delivered event.
    idle_timeout ends a stream after that many seconds without content,
    measured only after the response headers have been received.
    """
    params: Dict[str, Any] = {"tail": str(tail)}
    base_url = None
    if log_type == "all":
        base_url = host_api_base_url()
        route = Route("GET", "/v1/pod/{pod_id}/logs", pod_id=pod_id)
        params.update(stream="true", type=log_type)
    else:
        if endpoint_id is None:
            route = Route("GET", "/v2/pods/{pod_id}/logs", pod_id=pod_id)
        else:
            route = Route(
                "GET",
                "/v2/serverless/{endpoint_id}/workers/{pod_id}/logs",
                endpoint_id=endpoint_id,
                pod_id=pod_id,
            )
        if log_type:
            params["source"] = log_type
    if since:
        params["since"] = since
    headers = _headers()
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id

    client_timeout = aiohttp.ClientTimeout(total=STREAM_TIMEOUT_SECONDS, sock_read=60)
    async with HTTPClient(base_url=base_url) as client:
        async with client.stream(
            route, params=params, headers=headers, timeout=client_timeout
        ) as resp:
            await raise_for_status(resp)
            event_id = None
            data = []
            content = resp.content.__aiter__()
            while True:
                try:
                    raw = await asyncio.wait_for(anext(content), timeout=idle_timeout)
                except StopAsyncIteration:
                    return
                except asyncio.TimeoutError:
                    if idle_timeout is None:
                        raise
                    return
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line:
                    field, _, value = line.partition(":")
                    if value.startswith(" "):
                        value = value[1:]
                    if field == "id" and "\x00" not in value:
                        event_id = value
                    elif field == "data":
                        data.append(value)
                    continue
                if not data:
                    continue
                try:
                    event = json.loads("\n".join(data))
                except json.JSONDecodeError:
                    continue
                finally:
                    data = []
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
