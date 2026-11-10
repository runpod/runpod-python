"""browser-approval and email-verification login flows."""

import asyncio
import base64
import datetime as dt
import hashlib
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import quote
from uuid import uuid4

import aiohttp

from runpod._hosts import console_url
from runpod.api.rest import _build_url
from runpod.user_agent import USER_AGENT

import aiohttp

from ..error import QueryError
from .api import AppsApiClient
from .errors import AppError


MAX_KEY_ATTEMPTS = 3

_CLOSED_REASONS = {
    "DENIED": "login was denied in the browser",
    "EXPIRED": "login request expired",
}


class LoginError(AppError):
    pass


class _VerificationCodeRejected(LoginError):
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
                    raise _VerificationCodeRejected("verification code rejected")
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


def pending_agent_login_path(profile: str) -> Path:
    from runpod.cli.groups.config.functions import CREDENTIAL_FILE

    profile_id = hashlib.sha256(profile.encode("utf-8")).hexdigest()
    return Path(CREDENTIAL_FILE).parent / f"login-{profile_id}.json"


def save_agent_login(attempt: dict, profile: str) -> None:
    path = pending_agent_login_path(profile)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as pending:
            json.dump(attempt, pending)
        os.replace(temporary, path)
    except BaseException:
        os.unlink(temporary)
        raise


def load_agent_login(profile: str) -> dict:
    try:
        attempt = json.loads(
            pending_agent_login_path(profile).read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        raise LoginError(
            "no pending login for this profile. Run runpod login --agent --email ADDRESS"
        ) from None
    except ValueError:
        raise LoginError(
            "invalid pending login. Run runpod login --agent --email ADDRESS to request a new code"
        ) from None
    if not isinstance(attempt, dict) or any(
        not isinstance(attempt.get(field), str) or not attempt[field]
        for field in ("attempt_id", "verifier", "api_url")
    ):
        raise LoginError(
            "invalid pending login. Run runpod login --agent --email ADDRESS to request a new code"
        )
    return attempt


def _agent_session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30),
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        cookie_jar=aiohttp.DummyCookieJar(),
    )


async def start_agent_login(email: str) -> dict:
    """request an emailed code and return its proof-of-possession state."""
    verifier = secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    path = "/v2/signup/authentication-attempts"

    async with _agent_session() as session:
        attempt = await _post_authentication_attempt(
            session,
            path,
            {"email": email, "keyName": "runpod-cli-agent", "codeChallenge": challenge},
            creating=True,
        )
        attempt_id = attempt.get("attemptId")
        if not isinstance(attempt_id, str) or not attempt_id:
            raise LoginError("auth attempt failed to initialize")

    return {"attempt_id": attempt_id, "verifier": verifier, "api_url": _build_url(path)}


async def verify_agent_login(attempt: dict, code: str) -> str:
    """exchange a code and its saved verifier for an api key."""
    path = "/v2/signup/authentication-attempts"
    if attempt["api_url"] != _build_url(path):
        raise LoginError("pending login belongs to a different API. Request a new code")
    async with _agent_session() as session:
        result = await _post_authentication_attempt(
            session,
            f"{path}/{quote(attempt['attempt_id'], safe='')}/verify",
            {"code": code, "codeVerifier": attempt["verifier"]},
        )
        credential = result.get("credential")
        api_key = credential.get("apiKey") if isinstance(credential, dict) else None
        if not isinstance(api_key, str) or not api_key.strip():
            raise LoginError("email sign-in response did not contain an API key")
        return api_key


async def agent_login(email: str, *, read_code: Callable[[], str]) -> str:
    """exchange an emailed code for an api key bound to this login's verifier."""
    attempt = await start_agent_login(email)
    while True:
        code = read_code()
        try:
            return await verify_agent_login(attempt, code)
        except _VerificationCodeRejected:
            continue


def _parse_expires_at(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def auth_url(request_id: str) -> str:
    return f"{console_url()}/flash/login?request={request_id}"


async def _closed_reason(client: AppsApiClient, request_id: str) -> Optional[str]:
    """why the request can no longer produce a key, or None to keep prompting."""
    try:
        request = await client.get_auth_request_status(request_id)
    except (QueryError, aiohttp.ClientError, OSError, asyncio.TimeoutError):
        return None
    return _CLOSED_REASONS.get(request.get("status"))


async def browser_login(
    *,
    prompt_key: Callable[[], str],
    on_url: Optional[Callable[[str], None]] = None,
    on_rejected: Optional[Callable[[str], None]] = None,
    api: Optional[AppsApiClient] = None,
    max_attempts: int = MAX_KEY_ATTEMPTS,
) -> str:
    """run the browser-approval flow and return the pasted api key.

    on_url is called with the approval url as soon as the request is
    created (the caller renders/opens it). the console shows the new key
    only to the signed-in approver, so the user pastes it via prompt_key.
    on_rejected is called with a reason before each re-prompt.
    """
    client = api or AppsApiClient()
    request: Dict[str, Any] = await client.create_auth_request()
    request_id = request.get("id")

    if not request_id:
        raise LoginError("auth request failed to initialize")

    if on_url is not None:
        on_url(auth_url(request_id))

    reason = "no api key entered"
    for attempt in range(max_attempts):
        api_key = (prompt_key() or "").strip()
        if not api_key:
            reason = "no api key entered"
        elif await client.verify_api_key(api_key):
            return api_key
        else:
            reason = "api key was rejected"
        closed = await _closed_reason(client, request_id)
        if closed:
            raise LoginError(f"{closed}; run login again")
        if on_rejected is not None and attempt < max_attempts - 1:
            on_rejected(reason)

    raise LoginError(f"{reason}; run login again")
