"""container registry credentials for private images.

credentials are pure provision-time config referenced by name:

    @app.queue(image="ghcr.io/me/private:latest", registry_auth="my-ghcr")
    def infer(): ...

create and manage them with `rp registry add/list/delete`. resolution
(name -> containerRegistryAuthId) happens when the resource provisions.
"""

import logging
from typing import Optional

from .errors import AppError

log = logging.getLogger(__name__)


class RegistryAuthError(AppError):
    pass


async def resolve_registry_auth(
    name: Optional[str], api=None
) -> Optional[str]:
    """resolve a credential name (or id) to a containerRegistryAuthId."""
    if not name:
        return None
    if api is None:
        from .api import AppsApiClient

        api = AppsApiClient()
    creds = await api.list_registry_auths()
    for cred in creds:
        if cred["id"] == name:
            return cred["id"]
    matches = [c for c in creds if c.get("name") == name]
    if len(matches) > 1:
        raise RegistryAuthError(
            f"multiple registry credentials named '{name}' "
            f"({', '.join(m['id'] for m in matches)}); reference by id"
        )
    if matches:
        return matches[0]["id"]
    available = ", ".join(sorted(c["name"] for c in creds)) or "(none)"
    raise RegistryAuthError(
        f"registry credential '{name}' not found. available: {available}. "
        f"create one with `rp registry add {name}`"
    )
