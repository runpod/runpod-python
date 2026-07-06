"""browser-approval login flow.

opens a console url tied to a short-lived auth request; the api key
lands when the user approves in the browser. no credentials are sent
before approval, so an expired local key can never poison the flow.
"""

import asyncio
import datetime as dt
import os
from typing import Any, Callable, Dict, Optional

from .api import AppsApiClient
from .errors import AppError

CONSOLE_URL = os.environ.get("RUNPOD_CONSOLE_URL", "https://console.runpod.io")

POLL_INTERVAL_SECONDS = 2.0
DEFAULT_TIMEOUT_SECONDS = 600.0

_APPROVED_STATUSES = frozenset({"APPROVED", "CONSUMED"})
_FAILED_STATUSES = frozenset({"DENIED", "EXPIRED"})


class LoginError(AppError):
    pass


def _parse_expires_at(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def auth_url(request_id: str) -> str:
    return f"{CONSOLE_URL}/flash/login?request={request_id}"


async def browser_login(
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    on_url: Optional[Callable[[str], None]] = None,
    api: Optional[AppsApiClient] = None,
) -> str:
    """run the browser-approval flow and return the granted api key.

    on_url is called with the approval url as soon as the request is
    created (the caller renders/opens it).
    """
    client = api or AppsApiClient()
    request: Dict[str, Any] = await client.create_auth_request()
    request_id = request.get("id")
    if not request_id:
        raise LoginError("auth request failed to initialize")

    if on_url is not None:
        on_url(auth_url(request_id))

    deadline = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
        seconds=timeout_seconds
    )
    expires_at = _parse_expires_at(request.get("expiresAt"))
    if expires_at and expires_at < deadline:
        deadline = expires_at

    while True:
        status_payload = await client.get_auth_request_status(request_id)
        status = status_payload.get("status")
        api_key = status_payload.get("apiKey")

        if api_key and status in _APPROVED_STATUSES:
            return api_key
        if status in _FAILED_STATUSES:
            raise LoginError(f"login {status.lower()}")
        if status == "CONSUMED":
            raise LoginError("login request was already used")
        if dt.datetime.now(dt.timezone.utc) >= deadline:
            raise LoginError("login timed out")

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
