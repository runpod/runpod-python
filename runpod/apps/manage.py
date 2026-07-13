"""app and environment lifecycle: list, inspect, undeploy, delete.

everything resolves server-side (the flash app registry is the source
of truth); nothing is tracked locally. undeploying an environment
deletes its endpoints first so no orphaned workers keep billing.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .api import AppsApiClient
from .errors import AppError
from .utils.events import emit

log = logging.getLogger(__name__)


class AppNotFound(AppError):
    def __init__(self, app_name: str):
        super().__init__(
            f"no app named '{app_name}' found. run `rp deploy` to create one."
        )


class EnvironmentNotFound(AppError):
    def __init__(self, app_name: str, env_name: str):
        super().__init__(
            f"no environment '{env_name}' in app '{app_name}'."
        )


@dataclass
class UndeployResult:
    """outcome of tearing down one environment."""

    endpoints_deleted: int = 0
    environment_deleted: bool = False
    app_deleted: bool = False
    failures: List[str] = field(default_factory=list)


async def list_apps(api: Optional[AppsApiClient] = None) -> List[Dict]:
    """all apps with their environments, newest first."""
    client = api or AppsApiClient()
    apps = await client.list_apps()
    return sorted(apps, key=lambda a: a.get("name") or "")


async def get_app(
    app_name: str, api: Optional[AppsApiClient] = None
) -> Dict:
    client = api or AppsApiClient()
    app = await client.get_app_by_name(app_name)
    if app is None:
        raise AppNotFound(app_name)
    return app


async def get_environment(
    app_name: str, env_name: str, api: Optional[AppsApiClient] = None
) -> Dict:
    client = api or AppsApiClient()
    env = await client.get_environment_by_name(app_name, env_name)
    if env is None:
        raise EnvironmentNotFound(app_name, env_name)
    return env


async def undeploy_environment(
    app_name: str,
    env_name: str,
    *,
    api: Optional[AppsApiClient] = None,
    delete_env: bool = True,
    events: Optional[object] = None,
) -> UndeployResult:
    """tear down an environment: endpoints first, then the environment.

    events, when given, receives cleanup_started(total), deleting(name),
    deleted(name), delete_failed(name).
    """
    client = api or AppsApiClient()
    env = await get_environment(app_name, env_name, api=client)

    result = UndeployResult()
    endpoints = env.get("endpoints") or []
    emit(events, "cleanup_started", len(endpoints))
    for endpoint in endpoints:
        name = endpoint.get("name") or endpoint.get("id")
        emit(events, "deleting", name)
        try:
            await client.delete_endpoint(endpoint["id"])
            result.endpoints_deleted += 1
            emit(events, "deleted", name)
            log.info("deleted endpoint %s (%s)", name, endpoint["id"])
        except Exception as exc:  # noqa: BLE001 - collected for the caller
            result.failures.append(f"endpoint {name}: {exc}")
            emit(events, "delete_failed", name)

    if delete_env and not result.failures:
        try:
            await client.delete_environment(env["id"])
            result.environment_deleted = True
            log.info("deleted environment %s (%s)", env_name, env["id"])
        except Exception as exc:  # noqa: BLE001 - collected for the caller
            result.failures.append(f"environment {env_name}: {exc}")

    return result


async def delete_app(
    app_name: str,
    *,
    api: Optional[AppsApiClient] = None,
    events: Optional[object] = None,
) -> UndeployResult:
    """delete an app after undeploying every environment in it."""
    client = api or AppsApiClient()
    app = await get_app(app_name, api=client)

    result = UndeployResult()
    for env in app.get("flashEnvironments") or []:
        env_result = await undeploy_environment(
            app_name, env["name"], api=client, events=events
        )
        result.endpoints_deleted += env_result.endpoints_deleted
        result.failures.extend(env_result.failures)

    if not result.failures:
        try:
            await client.delete_app(app["id"])
            result.app_deleted = True
            log.info("deleted app %s (%s)", app_name, app["id"])
        except Exception as exc:  # noqa: BLE001 - collected for the caller
            result.failures.append(f"app {app_name}: {exc}")

    return result
