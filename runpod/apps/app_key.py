"""per-app api keys for workers.

every resource gets RUNPOD_API_KEY from a platform secret holding an api
key minted for its app and environment, unless its env sets one. the key
value travels only from key creation into the secret; templates carry the
secret reference and the platform decrypts it when a worker boots.

one key serves an app environment across deploys. dev sessions share a
separate key per app.
"""

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional

from ..error import AuthenticationError, QueryError
from .secret import Secret

APP_KEY_ENV = "RUNPOD_API_KEY"
log = logging.getLogger(__name__)

# what flash runtime does from a worker: call endpoints and list them
# (serverless:read), launch and terminate task pods (pods), resolve
# task mounts (volumes), and resolve registry credentials and secret
# references for task pods (secrets:read)
APP_KEY_SCOPES = [
    "pods:read",
    "pods:write",
    "secrets:read",
    "serverless:read",
    "volumes:read",
    "volumes:write",
]

# api key names are limited to 64 characters
_MAX_NAME = 64
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def app_key_name(app_name: str, env_name: Optional[str] = None) -> str:
    """secret and api key name for an app environment, or for the app's
    dev sessions when env_name is None."""
    if env_name is None:
        raw = f"flash-dev-key.{app_name}"
    else:
        raw = f"flash-app-key.{app_name}.{env_name}"
    name = _UNSAFE.sub("-", raw).lower()
    identity = json.dumps([app_name, env_name], ensure_ascii=True)
    digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
    return f"{name[: _MAX_NAME - len(digest) - 1]}.{digest}"


def needs_app_key(spec) -> bool:
    """true when a resource does not supply its own credentials through env."""
    return APP_KEY_ENV not in (spec.env or {})


async def _find_secret(api, name: str) -> Optional[Dict[str, Any]]:
    for secret in await api.list_secrets(name=name):
        if secret.get("name") == name:
            return secret
    return None


async def _key_ids(api, name: str) -> List[str]:
    return [k["id"] for k in await api.list_api_keys() if k.get("name") == name]


async def _secret_has_live_key(
    api, name: str, secret: Dict[str, Any], exclude_key_id: Optional[str] = None
) -> bool:
    key_ids = [
        key_id for key_id in await _key_ids(api, name) if key_id != exclude_key_id
    ]
    owner = re.match(r"api key (\S+) for ", secret.get("description") or "")
    return owner[1] in key_ids if owner else bool(key_ids)


async def ensure_app_key(api, app_name: str, env_name: Optional[str] = None) -> Secret:
    """the secret holding the app environment's api key, minting the key
    and the secret on first use. refresh preserves other same-name keys for
    concurrent deployments; teardown revokes every matching key."""
    name = app_key_name(app_name, env_name)
    secret = await _find_secret(api, name)
    if secret:
        if await _secret_has_live_key(api, name, secret):
            return Secret(name)
        try:
            await api.delete_secret(secret["id"])
        except QueryError as exc:
            if exc.status_code != 404:
                raise
    try:
        key = await api.create_api_key(name, APP_KEY_SCOPES)
    except (AuthenticationError, QueryError) as exc:
        if not (
            isinstance(exc, AuthenticationError)
            or exc.status_code in (401, 403)
            or (
                exc.status_code in (None, 400)
                and any(
                    reason in str(exc).casefold()
                    for reason in (
                        "not grantable",
                        "permission",
                        "unauthorized",
                        "forbidden",
                    )
                )
            )
        ):
            raise
        exc.message = (
            f"{exc}. Automatic worker credentials require API key creation "
            "permission and permission to grant: "
            + ", ".join(APP_KEY_SCOPES)
            + ". Use a caller with those permissions, or set RUNPOD_API_KEY "
            "in each resource's env to an appropriate scoped key or Secret."
        )
        raise
    target = f"{app_name}/{env_name}" if env_name else f"{app_name} dev sessions"
    description = f"api key {key['id']} for {target} workers"
    try:
        await api.create_secret(name, key["rawKey"], description=description)
    except BaseException as create_error:
        try:
            secret = await _find_secret(api, name)
        except Exception:
            log.warning(
                "could not reconcile secret %s; retained associated api key for cleanup",
                name,
            )
            raise create_error from None
        owns_secret = secret and secret.get("description") == description
        if (
            secret
            and not secret.get("description")
            and not (
                isinstance(create_error, QueryError) and create_error.status_code == 409
            )
        ):
            log.warning(
                "secret %s has no ownership metadata; retained associated api key for cleanup",
                name,
            )
            raise
        if not owns_secret:
            try:
                await api.delete_api_key(key["id"])
            except Exception:
                log.warning(
                    "could not revoke api key for secret %s; retry credential cleanup",
                    name,
                )
        if secret and isinstance(create_error, Exception):
            try:
                if await _secret_has_live_key(
                    api, name, secret, None if owns_secret else key["id"]
                ):
                    return Secret(name)
            except Exception:
                log.warning("could not validate the api key for secret %s", name)
                raise create_error from None
        raise
    return Secret(name)


async def delete_app_key(api, app_name: str, env_name: Optional[str] = None) -> bool:
    """remove the app environment's secret and revoke its keys. returns
    whether anything existed."""
    name = app_key_name(app_name, env_name)
    secret = await _find_secret(api, name)
    if secret is not None:
        await api.delete_secret(secret["id"])
    key_ids = await _key_ids(api, name)
    for key_id in key_ids:
        await api.delete_api_key(key_id)
    return secret is not None or bool(key_ids)
