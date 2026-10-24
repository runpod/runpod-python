"""browser-approval and email-verification login flows."""

import asyncio
import base64
import datetime as dt
import hashlib
import os
import secrets
from typing import Any, Callable, Dict, Optional
from urllib.parse import quote
from uuid import uuid4

import aiohttp

from runpod.api.rest import _build_url
from runpod.user_agent import USER_AGENT

from .api import AppsApiClient
from .errors import AppError

CONSOLE_URL = os.environ.get("RUNPOD_CONSOLE_URL", "https://console.runpod.io")

POLL_INTERVAL_SECONDS = 2.0
DEFAULT_TIMEOUT_SECONDS = 600.0

_APPROVED_STATUSES = frozenset({"APPROVED", "CONSUMED"})
_FAILED_STATUSES = frozenset({"DENIED", "EXPIRED"})


class LoginError(AppError):
    pass


async def _post_authentication_attempt(
    session: aiohttp.ClientSession,
    path: str,
    body: dict,
    *,
    creating: bool = False,
) -> dict:
    try:
        async with session.post(
            _build_url(path),
            json=body,
            headers={"Idempotency-Key": str(uuid4())} if creating else {},
            allow_redirects=False,
        ) as response:
            if response.status != (201 if creating else 200):
                if response.status == 403:
                    raise LoginError(
                        "email sign-in is not allowed for this account; sign in "
                        "through the console and use runpod login --api-key"
                    )
                if response.status == 404:
                    if creating:
                        raise LoginError("email sign-in is unavailable on this API")
                    raise LoginError(
                        "login attempt expired, was already used, or is locked; "
                        "start login again"
                    )
                if response.status == 422:
                    if creating:
                        raise LoginError(
                            "email sign-in request rejected; check --email"
                        )
                    raise LoginError("verification code rejected; start login again")
                if response.status == 429:
                    raise LoginError(
                        "too many login attempts; wait before trying again"
                    )
                raise LoginError(f"email sign-in failed (HTTP {response.status})")
            payload = await response.json()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise LoginError(
            "could not complete email sign-in; check your connection"
        ) from None
    except ValueError:
        raise LoginError("invalid response from the email sign-in API") from None

    if not isinstance(payload, dict):
        raise LoginError("invalid response from the email sign-in API")
    return payload


async def agent_login(email: str, *, read_code: Callable[[], str]) -> str:
    """exchange an emailed code for an api key bound to this login's verifier."""
    verifier = secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    path = "/v2/signup/authentication-attempts"

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30),
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        cookie_jar=aiohttp.DummyCookieJar(),
    ) as session:
        attempt = await _post_authentication_attempt(
            session,
            path,
            {"email": email, "keyName": "runpod-cli-agent", "codeChallenge": challenge},
            creating=True,
        )
        attempt_id = attempt.get("attemptId")
        if not isinstance(attempt_id, str) or not attempt_id:
            raise LoginError("auth attempt failed to initialize")

        code = read_code()
        result = await _post_authentication_attempt(
            session,
            f"{path}/{quote(attempt_id, safe='')}/verify",
            {"code": code, "codeVerifier": verifier},
        )
        credential = result.get("credential")
        api_key = credential.get("apiKey") if isinstance(credential, dict) else None
        if not isinstance(api_key, str) or not api_key.strip():
            raise LoginError("email sign-in response did not contain an API key")
        return api_key


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

    deadline = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=timeout_seconds)
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
