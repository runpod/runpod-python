"""dev session: ephemeral live endpoints for `rp dev`.

lifecycle: get-or-create endpoints named dev-{app}-{resource} on the
generic worker images (adopting leftovers from a killed session), run
the local entrypoint, and delete every session endpoint on exit. the
api is the only source of truth; nothing is persisted locally.
"""

import asyncio
import logging
import sys
from typing import Dict, List, Optional, Union

from .api import AppsApiClient
from .app import App
from .handles import ApiHandle, FunctionHandle
from .spec import ResourceKind, ResourceSpec
from .targets import LiveTarget

log = logging.getLogger(__name__)

DEV_PREFIX = "dev"

# worker runtime images by (kind, is_cpu); tag pinned via env override
DEFAULT_IMAGES = {
    ("queue", False): "runpod/flash:py3.12-latest",
    ("queue", True): "runpod/flash-cpu:py3.12-latest",
    ("api", False): "runpod/flash-lb:py3.12-latest",
    ("api", True): "runpod/flash-lb-cpu:py3.12-latest",
}


def dev_endpoint_name(app_name: str, resource_name: str) -> str:
    return f"{DEV_PREFIX}-{app_name}-{resource_name}"


def _image_for(spec: ResourceSpec) -> str:
    import os

    override = os.getenv("RUNPOD_DEV_IMAGE")
    if override:
        return override
    key = (spec.kind.value, spec.is_cpu)
    image = DEFAULT_IMAGES.get(key)
    if image is None:
        raise ValueError(f"no dev image for resource kind {spec.kind.value}")
    return image


# cpu serverless is only stocked in EU-RO-1
CPU_LOCATIONS = "EU-RO-1"


def _endpoint_input(app: App, spec: ResourceSpec) -> Dict:
    """build the saveEndpoint payload for a live dev endpoint.

    the template is nested in the saveEndpoint input so it is bound to
    the endpoint and cascades on deleteEndpoint.
    """
    payload: Dict = {
        "name": dev_endpoint_name(app.name, spec.name),
        "workersMin": spec.workers[0],
        "workersMax": spec.workers[1],
        "idleTimeout": spec.idle_timeout,
        "scalerType": (
            "REQUEST_COUNT" if spec.kind is ResourceKind.API else "QUEUE_DELAY"
        ),
        "scalerValue": 4,
        "template": {
            "name": f"{dev_endpoint_name(app.name, spec.name)}-template",
            "imageName": _image_for(spec),
            "containerDiskInGb": 10,
            "dockerArgs": "",
            "env": [
                {"key": k, "value": v} for k, v in (spec.env or {}).items()
            ],
        },
    }
    if spec.kind is ResourceKind.API:
        payload["type"] = "LB"
    if spec.datacenter:
        payload["locations"] = ",".join(spec.datacenter)
    if spec.is_cpu:
        payload["instanceIds"] = spec.cpu
        if not spec.datacenter:
            payload["locations"] = CPU_LOCATIONS
    else:
        payload["gpuIds"] = ",".join(spec.gpu or ["any"])
        payload["gpuCount"] = spec.gpu_count
    return payload


class DevSession:
    """owns the live endpoints for one `rp dev` invocation."""

    def __init__(self, apps: List[App], api: Optional[AppsApiClient] = None):
        self.apps = apps
        self.api = api or AppsApiClient()
        # endpoint ids created or adopted by this session
        self._endpoint_ids: List[str] = []

    def _provisionable(
        self, app: App
    ) -> List[Union[FunctionHandle, ApiHandle]]:
        """endpoints only; tasks have no standing infra to manage."""
        return [
            h
            for h in app.resources.values()
            if h.spec.kind in (ResourceKind.QUEUE, ResourceKind.API)
        ]

    async def start(self) -> None:
        """provision (or adopt) a live endpoint per queue/api resource and
        register the targets on each app."""
        existing = {e["name"]: e for e in await self.api.list_my_endpoints()}

        for app in self.apps:
            for handle in self._provisionable(app):
                spec = handle.spec
                name = dev_endpoint_name(app.name, spec.name)

                found = existing.get(name)
                if found:
                    endpoint_id = found["id"]
                    log.info("adopted existing dev endpoint %s (%s)", name, endpoint_id)
                else:
                    result = await self.api.save_endpoint(
                        _endpoint_input(app, spec)
                    )
                    endpoint_id = result["id"]
                    log.info("provisioned dev endpoint %s (%s)", name, endpoint_id)

                self._endpoint_ids.append(endpoint_id)
                app._dev_targets[spec.name] = LiveTarget(endpoint_id)

    async def stop(self) -> None:
        """delete every endpoint this session created or adopted."""
        for endpoint_id in self._endpoint_ids:
            try:
                await self.api.delete_endpoint(endpoint_id)
                log.info("deleted dev endpoint %s", endpoint_id)
            except Exception as exc:
                log.warning(
                    "failed to delete dev endpoint %s: %s", endpoint_id, exc
                )
        self._endpoint_ids.clear()
        for app in self.apps:
            app._dev_targets.clear()

    async def __aenter__(self) -> "DevSession":
        await self.start()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.stop()
