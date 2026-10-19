"""per-app api keys for workers that make outbound calls.

a resource that declares remote_calls gets RUNPOD_API_KEY from a platform
secret holding an api key minted for its app and environment. the key
value travels only from key creation into the secret; templates carry the
secret reference and the platform decrypts it when a worker boots.

one key serves an app environment across deploys. dev sessions share a
separate key per app.
"""

import hashlib
import re
from typing import Any, Dict, List, Optional

from ..error import QueryError
from .errors import AppError
from .secret import Secret

APP_KEY_ENV = "RUNPOD_API_KEY"

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


class AppKeyError(AppError):
    pass


def app_key_name(app_name: str, env_name: Optional[str] = None) -> str:
    """secret and api key name for an app environment, or for the app's
    dev sessions when env_name is None."""
    if env_name is None:
        raw = f"runpod-dev-key.{app_name}"
    else:
        raw = f"runpod-app-key.{app_name}.{env_name}"
    name = _UNSAFE.sub("-", raw)
    if len(name) <= _MAX_NAME:
        return name
    digest = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f"{name[: _MAX_NAME - len(digest) - 1]}.{digest}"


def needs_app_key(spec) -> bool:
    """true when a resource makes outbound calls and does not supply its
    own credentials through env."""
    return spec.remote_calls and APP_KEY_ENV not in (spec.env or {})


async def _find_secret(api, name: str) -> Optional[Dict[str, Any]]:
    for secret in await api.list_secrets():
        if secret.get("name") == name:
            return secret
    return None


async def _key_ids(api, name: str) -> List[str]:
    return [k["id"] for k in await api.list_api_keys() if k.get("name") == name]


async def ensure_app_key(api, app_name: str, env_name: Optional[str] = None) -> Secret:
    """the secret holding the app environment's api key, minting the key
    and the secret on first use."""
    name = app_key_name(app_name, env_name)
    if await _find_secret(api, name):
        return Secret(name)
    key = await api.create_api_key(name, APP_KEY_SCOPES)
    target = f"{app_name}/{env_name}" if env_name else f"{app_name} dev sessions"
    try:
        await api.create_secret(
            name, key["rawKey"], description=f"api key for {target} workers"
        )
    except QueryError:
        await api.delete_api_key(key["id"])
        # a concurrent deploy may have created it first
        if not await _find_secret(api, name):
            raise
    return Secret(name)


async def rotate_app_key(api, app_name: str, env_name: Optional[str] = None) -> None:
    """mint a new key into the existing secret and revoke the old ones.

    workers read the secret when they boot, so running workers keep the
    revoked key until they restart.
    """
    name = app_key_name(app_name, env_name)
    secret = await _find_secret(api, name)
    if secret is None:
        raise AppKeyError(f"no app key '{name}' to rotate")
    stale = await _key_ids(api, name)
    key = await api.create_api_key(name, APP_KEY_SCOPES)
    await api.update_secret_value(secret["id"], key["rawKey"])
    for key_id in stale:
        await api.delete_api_key(key_id)


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
