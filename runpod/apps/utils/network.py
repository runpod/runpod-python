"""shared authentication and lightweight data-plane requests."""

import os
from typing import Any, Dict

import aiohttp


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
