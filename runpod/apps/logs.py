"""pod log access via the host api (hapi).

hosts expose per-pod container and system logs; this is the fastest
way to see why a worker is stuck (image pull progress, region errors,
crash output) without ssh. snapshot and sse streaming supported.

    GET https://hapi.runpod.net/v1/pod/{podId}/logs
        ?type=all|container|system
    GET .../logs?stream=true&type=...&tail=N&since=RFC3339
"""

import json
import os
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

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
    log_type: str = "all",
    tail: int = 100,
    since: Optional[str] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """follow a pod's logs as they arrive.

    yields {"source": "container"|"system", "line": str, "ts": str}
    parsed from the host's sse stream. ends when the host closes the
    stream (1h server-side cap) or the caller breaks out.
    """
    url = f"{HAPI_BASE}/v1/pod/{pod_id}/logs"
    params: Dict[str, Any] = {
        "stream": "true",
        "type": log_type,
        "tail": str(tail),
    }
    if since:
        params["since"] = since

    client_timeout = aiohttp.ClientTimeout(
        total=STREAM_TIMEOUT_SECONDS, sock_read=60
    )
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.get(
            url, params=params, headers=_headers()
        ) as resp:
            resp.raise_for_status()
            async for raw in resp.content:
                line = raw.decode("utf-8", "replace").strip()
                # sse frames: "data: {...}"; comments (heartbeats) start
                # with ":"
                if not line.startswith("data:"):
                    continue
                try:
                    yield json.loads(line[len("data:") :].strip())
                except json.JSONDecodeError:
                    continue


def tail_summary(logs: Dict[str, List[str]], lines: int = 20) -> str:
    """human-readable tail of a log snapshot, for error messages."""
    parts = []
    for source in ("system", "container"):
        entries = logs.get(source) or []
        if entries:
            parts.append(f"--- {source} (last {min(lines, len(entries))}) ---")
            parts.extend(entries[-lines:])
    return "\n".join(parts) if parts else "(no logs available)"
