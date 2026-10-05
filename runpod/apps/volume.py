"""storage resources and their execution-specific mount bindings."""

import logging
import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Dict, List, Optional, Tuple

from .errors import AppError
from .utils.client import default_client
from .utils.events import emit
from .utils.lookup import find_by_id_or_name

log = logging.getLogger(__name__)

DEFAULT_SIZE_GB = 50
ENDPOINT_MOUNT_PATH = Path("/runpod-volume")


class VolumeError(AppError):
    pass


class Volume(ABC):
    """a storage reference, independent of its attachment to a worker."""

    def __init__(self, reference: str):
        if not isinstance(reference, str) or not reference.strip():
            raise VolumeError("volume reference must be a non-empty string")
        self._reference = reference

    @property
    @abstractmethod
    def kind(self) -> str:
        """the storage backend for this reference."""

    @property
    def reference(self) -> str:
        return self._reference

    @property
    def path(self) -> Path:
        """the unique mount path for this volume in the current worker."""
        paths = _mount_bindings.get((self.kind, self.reference), ())
        if not paths:
            raise VolumeError(
                f"{self.kind} volume {self.reference!r} is not mounted "
                "in the current execution"
            )
        if len(paths) != 1:
            raise VolumeError(
                f"{self.kind} volume {self.reference!r} has multiple mounts; "
                "use an explicit mount path"
            )
        return paths[0]


class NetworkVolume(Volume):
    """a network volume resolved by name or id, with datacenter placement."""

    kind = "network"

    def __init__(
        self,
        name: str,
        *,
        size: int = DEFAULT_SIZE_GB,
        datacenter: Optional[str] = None,
        create: bool = True,
    ):
        super().__init__(name)
        self.size = size
        self.datacenter = datacenter
        self.create = create

    @property
    def name(self) -> str:
        return self.reference

    def __repr__(self) -> str:
        return f"<NetworkVolume {self.name!r} size={self.size}GB>"


class GlobalVolume(Volume):
    """a global volume resolved by name or id without datacenter placement."""

    kind = "global"

    def __init__(self, name: str, *, create: bool = True):
        super().__init__(name)
        self.create = create

    @property
    def name(self) -> str:
        return self.reference

    def __repr__(self) -> str:
        return f"<GlobalVolume {self.name!r}>"


def _mount_path(path: str) -> str:
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or path.startswith("//")
        or "\0" in path
        or ".." in PurePosixPath(path).parts
    ):
        raise VolumeError("mount paths must be absolute paths without '..'")
    return str(PurePosixPath(path))


def normalize_mounts(mounts: Optional[Mapping[str, Volume]]) -> Dict[str, Volume]:
    """copy and normalize an explicit path-to-volume mapping."""
    if mounts is None:
        return {}
    if not isinstance(mounts, Mapping):
        raise VolumeError("mounts must map absolute paths to volume objects")
    normalized = {}
    for path, volume in mounts.items():
        path = _mount_path(path)
        if not isinstance(volume, Volume):
            raise VolumeError("mounts require NetworkVolume or GlobalVolume references")
        if path in normalized:
            raise VolumeError(f"duplicate mount path {path!r}")
        normalized[path] = volume
    return normalized


_mount_bindings: Mapping[Tuple[str, str], Tuple[Path, ...]] = MappingProxyType({})


def _configure_mounts(bindings: List[Dict[str, str]]) -> None:
    """install resolved mounts for this worker before importing user code."""
    if not isinstance(bindings, list):
        raise VolumeError("worker mount bindings must be a list")
    resolved = {}
    for binding in bindings:
        if not isinstance(binding, Mapping):
            raise VolumeError("worker mount bindings must be objects")
        kind = binding.get("kind")
        reference = binding.get("reference")
        volume_id = binding.get("id")
        if kind not in ("network", "global") or any(
            not isinstance(value, str) or not value.strip()
            for value in (reference, volume_id)
        ):
            raise VolumeError("worker mount bindings require kind, reference, and id")
        path = Path(_mount_path(binding.get("path")))
        for key in ((kind, reference), (kind, volume_id)):
            paths = resolved.setdefault(key, [])
            if path not in paths:
                paths.append(path)
    global _mount_bindings
    _mount_bindings = MappingProxyType(
        {key: tuple(paths) for key, paths in resolved.items()}
    )


class VolumeResolver:
    """resolve storage references once per provisioning run."""

    def __init__(self, api=None, events: Optional[object] = None):
        self._api = api
        self.events = events
        self._resolved: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._stock = None

    async def _client(self):
        self._api = default_client(self._api)
        return self._api

    async def resolve(
        self, volume: Volume, specs: Optional[List] = None
    ) -> Dict[str, Any]:
        """resolve a reference, applying app placement to network storage."""
        specs = specs or []
        for spec in specs:
            spec.validate()
        key = (volume.kind, volume.reference)
        cached = self._resolved.get(key)
        if cached is not None:
            return cached
        if isinstance(volume, GlobalVolume):
            client = await self._client()
            record = find_by_id_or_name(
                await client.list_global_volumes(),
                volume.name,
                noun="global volumes",
                error=VolumeError,
            )
            if record is None:
                if not volume.create:
                    raise VolumeError(
                        f"global volume '{volume.name}' not found and create=False"
                    )
                record = await client.create_global_volume(name=volume.name)
                log.info("created global volume %s (%s)", volume.name, record["id"])
            resolved = {"id": record["id"]}
            self._resolved[key] = resolved
            return resolved
        if not isinstance(volume, NetworkVolume):
            raise VolumeError(f"unsupported volume type {type(volume).__name__}")

        client = await self._client()
        existing = await client.list_network_volumes()
        record = find_by_id_or_name(
            existing, volume.name, noun="volumes", error=VolumeError
        )
        if record is None and not volume.create:
            raise VolumeError(f"volume '{volume.name}' not found and create=False")

        dc = record["dataCenter"] if record is not None else volume.datacenter
        if specs:
            from .placement import StockMap, _hardware_keys, solve_placement

            if self._stock is None:
                self._stock = StockMap(client)
            await self._stock.fetch([k for spec in specs for k in _hardware_keys(spec)])
            dc = solve_placement(
                specs, self._stock, volume_name=volume.name, existing_dc=dc
            )
        elif not dc:
            raise VolumeError(
                f"creating network volume {volume.name!r} requires a datacenter "
                "when no app placement constraints are available"
            )

        if record is None:
            record = await client.create_network_volume(
                name=volume.name, size=volume.size, data_center_id=dc
            )
            emit(self.events, "volume_created", volume.name, volume.size, dc)
            log.info(
                "created volume %s (%s, %dGB, %s)",
                volume.name,
                record["id"],
                volume.size,
                dc,
            )
        resolved = {"id": record["id"], "dataCenterId": dc}
        self._resolved[key] = resolved
        return resolved

    async def resolve_mounts(
        self, mounts: Optional[Mapping[str, Volume]], specs: Optional[List] = None
    ) -> List[Dict[str, str]]:
        """resolve explicit attachments without changing their volume objects."""
        bindings = []
        for path, volume in normalize_mounts(mounts).items():
            sharing = [
                spec
                for spec in specs or []
                if any(
                    (ref.kind, ref.reference) == (volume.kind, volume.reference)
                    for ref in spec.mounts.values()
                )
            ]
            resolved = await self.resolve(volume, sharing)
            bindings.append(
                {
                    "kind": volume.kind,
                    "reference": volume.reference,
                    "path": path,
                    **resolved,
                }
            )
        return bindings


def validate_mounts(mounts: Mapping[str, Volume], kind: str, is_cpu: bool) -> None:
    """validate the mount capabilities of the target execution environment."""
    paths = [PurePosixPath(path) for path in mounts]
    for index, path in enumerate(paths):
        if any(
            path.is_relative_to(other) or other.is_relative_to(path)
            for other in paths[:index]
        ):
            raise VolumeError("mount paths must not overlap")
    for volume_kind in ("network", "global"):
        if sum(volume.kind == volume_kind for volume in mounts.values()) > 1:
            raise VolumeError(f"resources support at most one {volume_kind} volume")
    if kind == "sandbox":
        reserved = PurePosixPath("/etc/resolv.conf")
        if any(
            path.is_relative_to(reserved) or reserved.is_relative_to(path)
            for path in paths
        ):
            raise VolumeError("sandbox mounts must not overlap /etc/resolv.conf")
    if kind in ("queue", "api") and mounts:
        if len(mounts) != 1 or next(iter(mounts)) != str(ENDPOINT_MOUNT_PATH):
            raise VolumeError("endpoints support one volume mounted at /runpod-volume")
        if is_cpu and any(
            isinstance(volume, GlobalVolume) for volume in mounts.values()
        ):
            raise VolumeError("global volumes require a gpu endpoint")


def _bind_worker_mounts(
    payload: Dict[str, Any], bindings: List[Dict[str, str]]
) -> None:
    env = payload.setdefault("env", [])
    env[:] = [entry for entry in env if entry["key"] != "RUNPOD_MOUNTS"]
    env.append({"key": "RUNPOD_MOUNTS", "value": json.dumps(bindings)})


async def attach_pod_mounts(
    payload: Dict[str, Any], spec, resolver: VolumeResolver, specs: List
) -> None:
    """attach task storage and supply the worker's resolved filesystem bindings."""
    validate_mounts(spec.mounts, spec.kind.value, spec.is_cpu)
    bindings = await resolver.resolve_mounts(spec.mounts, specs)
    payload["volumeMounts"] = [
        {
            "volumeId": binding["id"],
            "volumeType": (
                "NETWORK_VOLUME"
                if binding["kind"] == "network"
                else "OBJECT_STORE_VOLUME"
            ),
            "mountPath": binding["path"],
        }
        for binding in bindings
    ]
    for binding in bindings:
        if binding["kind"] == "network":
            payload["dataCenterIds"] = [binding["dataCenterId"]]
    _bind_worker_mounts(payload, bindings)


async def attach_endpoint_volumes(
    payload: Dict[str, Any], spec, resolver: VolumeResolver, app
) -> None:
    """attach endpoint storage at the platform mount path."""
    validate_mounts(spec.mounts, spec.kind.value, spec.is_cpu)
    bindings = await resolver.resolve_mounts(
        spec.mounts, [handle.spec for handle in app.resources.values()]
    )
    payload["networkVolumeIds"] = [
        {"networkVolumeId": binding["id"]}
        for binding in bindings
        if binding["kind"] == "network"
    ]
    payload["volumes"] = [
        {"volumeId": binding["id"], "volumeType": "OBJECT_STORE_VOLUME"}
        for binding in bindings
        if binding["kind"] == "global"
    ]
    for binding in bindings:
        if binding["kind"] == "network":
            payload["locations"] = binding["dataCenterId"]
    _bind_worker_mounts(payload.setdefault("template", {}), bindings)
