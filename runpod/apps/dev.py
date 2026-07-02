"""dev session: ephemeral live endpoints for `rp dev`.

lifecycle: get-or-create endpoints named dev-{app}-{resource} on the
generic worker images (adopting leftovers from a killed session), run
the local entrypoint, and delete every session endpoint on exit. the
api is the only source of truth; nothing is persisted locally.
"""

import asyncio
import base64
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Union

from .api import AppsApiClient
from .app import App
from .handles import ApiHandle, FunctionHandle
from .spec import ResourceKind, ResourceSpec
from .targets import LiveTarget

log = logging.getLogger(__name__)

DEV_PREFIX = "dev"

# worker runtime images by (kind, is_cpu). queue images are built from
# runpod/runtimes/queue in this repo; api still rides the flash lb
# images until the lb runtime lands. RUNPOD_RUNTIME_TAG selects the
# image channel (latest, dev, or a pinned version).
import os as _os

_TAG = _os.environ.get("RUNPOD_RUNTIME_TAG", "latest")

DEFAULT_IMAGES = {
    ("queue", False): f"runpod/queue:py3.12-{_TAG}",
    ("queue", True): f"runpod/queue:py3.12-{_TAG}",
    ("api", False): f"runpod/api:py3.12-{_TAG}",
    ("api", True): f"runpod/api:py3.12-{_TAG}",
}


def dev_endpoint_name(app_name: str, resource_name: str) -> str:
    return f"{DEV_PREFIX}-{app_name}-{resource_name}"


def _image_for(spec: ResourceSpec) -> str:
    import os

    if spec.image:
        return spec.image
    override = os.getenv("RUNPOD_DEV_IMAGE")
    if override:
        return override
    key = (spec.kind.value, spec.is_cpu)
    image = DEFAULT_IMAGES.get(key)
    if image is None:
        raise ValueError(f"no dev image for resource kind {spec.kind.value}")
    return image


def _bootstrap_source() -> str:
    return (
        Path(__file__).parent.parent / "runtimes" / "bootstrap.py"
    ).read_text()


def _bootstrap_docker_args() -> str:
    """shell command that materializes and starts the bootstrap on a
    custom image."""
    return (
        "bash -c 'echo $RUNPOD_BOOTSTRAP_B64 | base64 -d > /bootstrap.py "
        "&& python3 /bootstrap.py'"
    )


def _cpu_locations(instance_ids: List[str]) -> str:
    """locations covering every requested cpu flavor's stock, limited to
    datacenters with storage and S3 support. the flash images are
    autocached on every host (host imagecache daemon, no DC filter), so
    the widest supported spread maximizes provisioning odds."""
    from .datacenter import CPU3_DATACENTERS, CPU5_DATACENTERS

    if any(i.startswith("cpu5") for i in instance_ids):
        return ",".join(dc.value for dc in CPU5_DATACENTERS)
    return ",".join(dc.value for dc in CPU3_DATACENTERS)

# template env var bumped on refresh; env is a version-triggering template
# property, so changing it recreates all workers server-side
GENERATION_ENV = "RUNPOD_DEV_GENERATION"


def _endpoint_input(app: App, spec: ResourceSpec, generation: int = 1) -> Dict:
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
        "flashBootType": "FLASHBOOT",
        "template": {
            "name": f"{dev_endpoint_name(app.name, spec.name)}-template",
            "imageName": _image_for(spec),
            "containerDiskInGb": 10,
            "dockerArgs": "",
            "env": [
                {"key": GENERATION_ENV, "value": str(generation)},
                *({"key": k, "value": v} for k, v in (spec.env or {}).items()),
            ],
        },
    }

    if spec.image:
        # custom image: inject the bootstrap so the worker runtime starts
        # regardless of what the image contains
        payload["template"]["env"].extend(
            [
                {
                    "key": "RUNPOD_BOOTSTRAP_B64",
                    "value": base64.b64encode(
                        _bootstrap_source().encode()
                    ).decode(),
                },
                {"key": "RUNPOD_RUNTIME_KIND", "value": spec.kind.value},
            ]
        )
        payload["template"]["dockerArgs"] = _bootstrap_docker_args()
    if spec.kind is ResourceKind.API:
        payload["type"] = "LB"
    if spec.datacenter:
        payload["locations"] = ",".join(spec.datacenter)
    if spec.is_cpu:
        payload["instanceIds"] = spec.cpu
        if not spec.datacenter:
            payload["locations"] = _cpu_locations(spec.cpu or [])
    else:
        payload["gpuIds"] = ",".join(spec.gpu or ["any"])
        payload["gpuCount"] = spec.gpu_count
    return payload


class DevSession:
    """owns the live endpoints for one `rp dev` invocation.

    lifecycle: adopt-or-create by name on start, reconcile on refresh
    (config updates + a generation bump that recreates workers so every
    request after a code change runs fully fresh), delete on stop.
    """

    def __init__(self, apps: List[App], api: Optional[AppsApiClient] = None):
        self.apps = apps
        self.api = api or AppsApiClient()
        self.generation = 1
        # endpoint name -> id for everything this session owns
        self._endpoints: Dict[str, str] = {}

    @property
    def _endpoint_ids(self) -> List[str]:
        return list(self._endpoints.values())

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
                payload = _endpoint_input(app, spec, self.generation)

                found = existing.get(name)
                if found:
                    # adopt: reconcile the leftover endpoint to the
                    # current spec instead of creating a duplicate
                    payload["id"] = found["id"]
                    result = await self.api.save_endpoint(payload)
                    endpoint_id = result["id"]
                    log.info("adopted dev endpoint %s (%s)", name, endpoint_id)
                else:
                    result = await self.api.save_endpoint(payload)
                    endpoint_id = result["id"]
                    log.info("provisioned dev endpoint %s (%s)", name, endpoint_id)

                self._endpoints[name] = endpoint_id
                app._dev_targets[spec.name] = LiveTarget(endpoint_id)

    async def refresh(self, apps: List[App]) -> None:
        """reconcile endpoints against a re-scanned set of apps.

        every surviving endpoint gets the new config plus a bumped
        generation env var; env is a version-triggering template
        property, so the platform recreates all workers and subsequent
        requests execute in fresh processes. added resources are
        provisioned, removed ones deleted."""
        self.generation += 1
        self.apps = apps

        desired: Dict[str, tuple] = {}
        for app in apps:
            for handle in self._provisionable(app):
                name = dev_endpoint_name(app.name, handle.spec.name)
                desired[name] = (app, handle)

        # delete endpoints whose resources disappeared
        for name in list(self._endpoints):
            if name not in desired:
                endpoint_id = self._endpoints.pop(name)
                try:
                    await self.api.delete_endpoint(endpoint_id)
                    log.info("deleted removed dev endpoint %s", name)
                except Exception as exc:
                    log.warning("failed to delete %s: %s", name, exc)

        # update survivors (config + generation bump) and create additions
        for name, (app, handle) in desired.items():
            payload = _endpoint_input(app, handle.spec, self.generation)
            existing_id = self._endpoints.get(name)
            if existing_id:
                payload["id"] = existing_id
            result = await self.api.save_endpoint(payload)
            endpoint_id = result["id"]
            self._endpoints[name] = endpoint_id
            app._dev_targets[handle.spec.name] = LiveTarget(endpoint_id)
            log.info(
                "refreshed dev endpoint %s (%s, generation %d)",
                name,
                endpoint_id,
                self.generation,
            )

    async def stop(self) -> None:
        """delete every endpoint this session owns."""
        for name, endpoint_id in list(self._endpoints.items()):
            try:
                await self.api.delete_endpoint(endpoint_id)
                log.info("deleted dev endpoint %s (%s)", name, endpoint_id)
            except Exception as exc:
                log.warning(
                    "failed to delete dev endpoint %s: %s", endpoint_id, exc
                )
        self._endpoints.clear()
        for app in self.apps:
            app._dev_targets.clear()

    async def __aenter__(self) -> "DevSession":
        await self.start()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.stop()
