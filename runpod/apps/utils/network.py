"""shared authentication and lightweight data-plane requests."""

import os
from typing import Any, Dict, Optional

import aiohttp


def runtime_headers() -> Optional[Dict[str, str]]:
    from ..context import deployed_identity

    if deployed_identity() is None:
        return None
    endpoint = os.getenv("RUNPOD_ENDPOINT_ID")
    pod = os.getenv("RUNPOD_POD_ID")
    if os.getenv("RUNPOD_FLASH_TASK") == "1":
        endpoint = None
    if not endpoint and not pod:
        raise RuntimeError("production flash runtime source identity is missing")
    key = os.getenv("RUNPOD_AI_API_KEY" if endpoint else "RUNPOD_API_KEY")
    if not key or key != key.strip():
        raise RuntimeError("production flash runtime credential is missing")
    if (endpoint or pod) != (endpoint or pod).strip():
        raise RuntimeError("production flash runtime source identity is malformed")
    return {
        "Authorization": f"Bearer {key}",
        "X-RunPod-Endpoint-Id" if endpoint else "X-RunPod-Pod-Id": endpoint or pod,
    }


def api_key() -> str:
    key = os.getenv("RUNPOD_API_KEY")
    if not key:
        import runpod

        key = runpod.api_key
    if not key:
        raise RuntimeError(
            "no api key configured. run `rp login` or set RUNPOD_API_KEY."
        )
    return key


def endpoint_url_base() -> str:
    import runpod

    return runpod.endpoint_url_base.rstrip("/")


async def get_json(url: str, headers: Dict[str, str], timeout: float) -> Dict[str, Any]:
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.get(url, headers=headers) as response:
            response.raise_for_status()
            return await response.json()
