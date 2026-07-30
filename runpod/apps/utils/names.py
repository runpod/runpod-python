"""stable names for app infrastructure."""

import hashlib

DEV_PREFIX = "dev"


def dev_endpoint_name(app_name: str, resource_name: str) -> str:
    """return an unambiguous endpoint name for an app resource."""
    digest = hashlib.sha256(f"{app_name}/{resource_name}".encode()).hexdigest()[:6]
    return f"{DEV_PREFIX}-{app_name}-{resource_name}-{digest}"
