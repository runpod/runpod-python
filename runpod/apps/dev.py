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

import os as _os


def dev_endpoint_name(app_name: str, resource_name: str) -> str:
    return f"{DEV_PREFIX}-{app_name}-{resource_name}"


def _resource_of(endpoint_name: str) -> str:
    """resource name from a dev endpoint name (last dash segment)."""
    return endpoint_name.rsplit("-", 1)[-1]


def _comparable(payload: Dict) -> Dict:
    """payload normalized for change detection.

    the generation env is bumped on every refresh by design; strip it so
    only real configuration differences count.
    """
    import copy

    clean = copy.deepcopy(payload)
    clean.pop("id", None)
    template = clean.get("template") or {}
    template["env"] = [
        e
        for e in template.get("env") or []
        if e.get("key") != GENERATION_ENV
    ]
    return clean


def _changed_fields(old: Dict, new: Dict) -> List[str]:
    """human-readable names of top-level payload fields that differ."""
    fields = []
    for key in sorted(set(old) | set(new)):
        if old.get(key) == new.get(key):
            continue
        if key == "template":
            t_old, t_new = old.get(key) or {}, new.get(key) or {}
            for sub in sorted(set(t_old) | set(t_new)):
                if t_old.get(sub) != t_new.get(sub):
                    fields.append("image" if sub == "imageName" else sub)
        else:
            fields.append(key)
    return fields


def _image_for(spec: ResourceSpec) -> str:
    """dev worker image: custom image, env override, or the builtin
    runtime image matched to the local python (dev requests carry
    pickled source, so client and worker versions should align)."""
    if spec.image:
        return spec.image
    override = _os.getenv("RUNPOD_DEV_IMAGE")
    if override:
        return override
    from .images import image_for_spec, local_python_version

    return image_for_spec(spec, python_version=local_python_version())


def _bootstrap_source() -> str:
    return (
        Path(__file__).parent.parent / "runtimes" / "bootstrap.py"
    ).read_text()


def _bootstrap_docker_args() -> str:
    """shell command that materializes and starts the bootstrap on a
    custom image."""
    from .shim import shell_launcher

    return shell_launcher("RUNPOD_BOOTSTRAP_B64", "/bootstrap.py")


def _render_env(env):
    from .secret import render_env

    return render_env(env)


def _client_api_key() -> str:
    from .targets import _api_key

    try:
        return _api_key()
    except RuntimeError:
        # payload construction must not require credentials (tests,
        # dry runs); the session itself fails loudly on its first call
        return ""


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
            "containerDiskInGb": 10 if spec.is_cpu else 30,
            "dockerArgs": "",
            "env": [
                {"key": GENERATION_ENV, "value": str(generation)},
                # nested .remote() calls from inside a dev worker need
                # credentials and the dev-session marker to resolve
                # sibling dev endpoints by name
                *(
                    [{"key": "RUNPOD_API_KEY", "value": key}]
                    if (key := _client_api_key())
                    else []
                ),
                {"key": "RUNPOD_DEV_APP", "value": app.name},
                *(
                    {"key": k, "value": v}
                    for k, v in _render_env(spec.env).items()
                ),
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
        # the bootstrap pip-installs the runpod package on bare images;
        # forward a pinned spec (e.g. a git branch during prerelease)
        package_spec = _os.environ.get("RUNPOD_PACKAGE_SPEC")
        if package_spec:
            payload["template"]["env"].append(
                {"key": "RUNPOD_PACKAGE_SPEC", "value": package_spec}
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
        from .gpu import gpu_ids_value

        payload["gpuIds"] = gpu_ids_value(spec.gpu)
        payload["gpuCount"] = spec.gpu_count
    return payload


class DevSession:
    """owns the live endpoints for one `rp dev` invocation.

    lifecycle: adopt-or-create by name on start, reconcile on refresh
    (config updates + a generation bump that recreates workers so every
    request after a code change runs fully fresh), delete on stop.

    `events`, when provided, receives lifecycle callbacks:
    provisioning(name, kind, hardware), adopted(name, id),
    ready(name, id), refreshed(name, generation), deleted(name).
    """

    def __init__(
        self,
        apps: List[App],
        api: Optional[AppsApiClient] = None,
        events: Optional[object] = None,
    ):
        self.apps = apps
        self.api = api or AppsApiClient()
        self.generation = 1
        self.events = events
        # endpoint name -> id for everything this session owns
        self._endpoints: Dict[str, str] = {}
        # endpoint name -> comparable payload, for refresh diffing
        self._payloads: Dict[str, Dict] = {}
        # volumes resolve once per session (placement is stable)
        self._volume_resolver = None

    def _emit(self, event: str, *args) -> None:
        handler = getattr(self.events, event, None)
        if handler is not None:
            handler(*args)

    async def _attach_volumes(self, payload: Dict, spec, app) -> None:
        """resolve the resource's volumes and registry auth onto a
        dev endpoint payload."""
        from .deploy import attach_endpoint_volumes
        from .registry import resolve_registry_auth
        from .volume import VolumeResolver

        if spec.registry_auth:
            auth_id = await resolve_registry_auth(
                spec.registry_auth, api=self.api
            )
            payload["template"]["containerRegistryAuthId"] = auth_id
        if not spec.volume:
            return
        if self._volume_resolver is None:
            self._volume_resolver = VolumeResolver(
                self.api, events=self.events
            )
        await attach_endpoint_volumes(
            payload, spec, self._volume_resolver, app
        )

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
        self._emit("session_starting")
        from .secret import secret_names, validate_secrets

        referenced = [
            name
            for app in self.apps
            for handle in app.resources.values()
            for name in secret_names(handle.spec.env)
        ]
        await validate_secrets(referenced, api=self.api)
        for app in self.apps:
            # task targets read the sink off the app at resolve time
            app._dev_events = self.events
        existing = {e["name"]: e for e in await self.api.list_my_endpoints()}

        for app in self.apps:
            for handle in self._provisionable(app):
                spec = handle.spec
                name = dev_endpoint_name(app.name, spec.name)
                payload = _endpoint_input(app, spec, self.generation)
                await self._attach_volumes(payload, spec, app)

                hardware = ",".join(spec.cpu or spec.gpu or ["any"])
                found = existing.get(name)
                if found:
                    # adopt: reconcile the leftover endpoint to the
                    # current spec instead of creating a duplicate
                    self._emit("adopted", spec.name, found["id"])
                    payload["id"] = found["id"]
                    result = await self.api.save_endpoint(payload)
                    endpoint_id = result["id"]
                    log.info("adopted dev endpoint %s (%s)", name, endpoint_id)
                else:
                    self._emit(
                        "provisioning", spec.name, spec.kind.value, hardware
                    )
                    result = await self.api.save_endpoint(payload)
                    endpoint_id = result["id"]
                    log.info("provisioned dev endpoint %s (%s)", name, endpoint_id)

                self._emit("ready", spec.name, endpoint_id)
                self._endpoints[name] = endpoint_id
                self._payloads[name] = _comparable(payload)
                app._dev_targets[spec.name] = LiveTarget(
                    endpoint_id,
                    spec.name,
                    events=self.events,
                    metrics_key=result.get("aiKey"),
                )

        self._emit("session_started")

    async def refresh(self, apps: List[App]) -> None:
        """reconcile endpoints against a re-scanned set of apps.

        every surviving endpoint gets the new config plus a bumped
        generation env var; env is a version-triggering template
        property, so the platform recreates all workers and subsequent
        requests execute in fresh processes. added resources are
        provisioned, removed ones deleted."""
        self.generation += 1
        self.apps = apps
        for app in apps:
            # re-scanned apps are fresh instances; re-attach the sink
            # so task lifecycle events keep rendering after reloads
            app._dev_events = self.events

        desired: Dict[str, tuple] = {}
        for app in apps:
            for handle in self._provisionable(app):
                name = dev_endpoint_name(app.name, handle.spec.name)
                desired[name] = (app, handle)

        # delete endpoints whose resources disappeared
        for name in list(self._endpoints):
            if name not in desired:
                endpoint_id = self._endpoints.pop(name)
                self._payloads.pop(name, None)
                try:
                    await self.api.delete_endpoint(endpoint_id)
                    self._emit("resource_removed", _resource_of(name))
                    log.info("deleted removed dev endpoint %s", name)
                except Exception as exc:
                    log.warning("failed to delete %s: %s", name, exc)

        # update survivors (config + generation bump) and create additions
        for name, (app, handle) in desired.items():
            payload = _endpoint_input(app, handle.spec, self.generation)
            await self._attach_volumes(payload, handle.spec, app)
            existing_id = self._endpoints.get(name)
            comparable = _comparable(payload)
            previous = self._payloads.get(name)
            if existing_id:
                payload["id"] = existing_id
            result = await self.api.save_endpoint(payload)
            endpoint_id = result["id"]
            self._endpoints[name] = endpoint_id
            self._payloads[name] = comparable
            app._dev_targets[handle.spec.name] = LiveTarget(
                endpoint_id,
                handle.spec.name,
                events=self.events,
                metrics_key=result.get("aiKey"),
            )
            spec = handle.spec
            hardware = ",".join(spec.cpu or spec.gpu or ["any"])
            if previous is None:
                self._emit(
                    "resource_added", spec.name, spec.kind.value, hardware
                )
            elif previous != comparable:
                self._emit(
                    "resource_changed",
                    spec.name,
                    _changed_fields(previous, comparable),
                )
            log.info(
                "refreshed dev endpoint %s (%s, generation %d)",
                name,
                endpoint_id,
                self.generation,
            )

    async def stop(self, events: Optional[object] = None) -> None:
        """delete every endpoint this session owns.

        events, when given, overrides the session sink for teardown
        rendering: cleanup_started(total), deleting(name), deleted(name),
        delete_failed(name).
        """
        sink = events if events is not None else self.events

        def _tell(event: str, *args) -> None:
            handler = getattr(sink, event, None)
            if handler is not None:
                handler(*args)

        pending = list(self._endpoints.items())
        _tell("cleanup_started", len(pending))
        for name, endpoint_id in pending:
            resource = _resource_of(name)
            _tell("deleting", resource)
            try:
                await self.api.delete_endpoint(endpoint_id)
                _tell("deleted", resource)
                log.info("deleted dev endpoint %s (%s)", name, endpoint_id)
            except Exception as exc:
                _tell("delete_failed", resource)
                log.warning(
                    "failed to delete dev endpoint %s: %s", endpoint_id, exc
                )
        self._endpoints.clear()
        for app in self.apps:
            app._dev_targets.clear()
            app._dev_events = None

    async def __aenter__(self) -> "DevSession":
        await self.start()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.stop()
