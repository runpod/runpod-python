"""network volumes: durable storage shared across resources.

a Volume is a lazy reference by name, resolved (and created when
missing) at provision time. workers see the volume at /runpod-volume.

    models = runpod.Volume("models")             # create if missing, 50GB
    models = runpod.Volume("models", size=100)

    @app.task(gpu="4090", volume=models)
    def train():
        torch.save(sd, models.path / "model.pt")

placement: a volume lives in exactly one datacenter, so everything
attached to it must schedule there. resolution runs the placement
solve over every resource sharing the volume (see runpod.apps.placement).
tasks/pods take one volume; endpoints may take several (one per
datacenter, locations derived from the volumes).
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import AppError
from .utils.client import default_client
from .utils.events import emit
from .utils.lookup import find_by_id_or_name

log = logging.getLogger(__name__)

DEFAULT_SIZE_GB = 50
# platform mount conventions: pods bind network volumes at /workspace,
# serverless endpoint workers at /runpod-volume
POD_MOUNT_PATH = Path("/workspace")
ENDPOINT_MOUNT_PATH = Path("/runpod-volume")


class VolumeError(AppError):
    pass


class Volume:
    """a named network volume reference, created on first use."""

    def __init__(
        self,
        name: str,
        *,
        size: int = DEFAULT_SIZE_GB,
        datacenter: Optional[str] = None,
        create: bool = True,
    ):
        if not name or not isinstance(name, str):
            raise VolumeError("volume name must be a non-empty string")
        self.name = name
        self.size = size
        self.datacenter = datacenter
        self.create = create

    @property
    def path(self) -> Path:
        """where the volume appears in the current worker.

        context-sensitive because the platform mounts differ: task
        pods use /workspace, endpoint workers /runpod-volume. resolved
        at access time inside the worker, so the same function body
        works from either.
        """
        import os

        if os.environ.get("RUNPOD_ENDPOINT_ID"):
            return ENDPOINT_MOUNT_PATH
        return POD_MOUNT_PATH

    def __repr__(self) -> str:
        return f"<Volume {self.name!r} size={self.size}GB>"


def _as_volume(ref: Any) -> Volume:
    if isinstance(ref, Volume):
        return ref
    if isinstance(ref, str):
        return Volume(ref)
    raise VolumeError(
        f"volume must be a runpod.Volume or name/id string, "
        f"got {type(ref).__name__}"
    )


def volume_list(spec_volume: Any) -> List[Volume]:
    """normalize a spec's volume field to a list of Volume refs."""
    if spec_volume is None:
        return []
    if isinstance(spec_volume, (list, tuple)):
        return [_as_volume(v) for v in spec_volume]
    return [_as_volume(spec_volume)]


class VolumeResolver:
    """resolves every volume in an app once per provision run.

    resolution: find by id or name; when missing, run the placement
    solve over all resources sharing the volume and create it in the
    chosen datacenter. results cache by name so each volume resolves
    exactly once regardless of how many resources reference it.
    """

    def __init__(self, api=None, events: Optional[object] = None):
        self._api = api
        self.events = events
        self._resolved: Dict[str, Dict[str, Any]] = {}
        self._stock = None

    async def _client(self):
        self._api = default_client(self._api)
        return self._api

    async def resolve(
        self, volume: Volume, specs: List
    ) -> Dict[str, Any]:
        """resolve one volume to {'id', 'dataCenterId'}.

        specs are all resource specs that attach this volume; they
        drive placement for creation and validate an existing DC.
        """
        cached = self._resolved.get(volume.name)
        if cached is not None:
            return cached

        from .placement import StockMap, solve_placement

        client = await self._client()
        existing = await client.list_network_volumes()

        record = find_by_id_or_name(
            existing, volume.name, noun="volumes", error=VolumeError
        )

        if self._stock is None:
            self._stock = StockMap(client)
        from .placement import _hardware_keys

        keys = [k for spec in specs for k in _hardware_keys(spec)]
        await self._stock.fetch(keys)

        if record is not None:
            # existing volume: its DC is a hard constraint
            dc = solve_placement(
                specs,
                self._stock,
                volume_name=volume.name,
                existing_dc=record["dataCenterId"],
            )
            resolved = {"id": record["id"], "dataCenterId": dc}
            self._resolved[volume.name] = resolved
            return resolved

        if not volume.create:
            raise VolumeError(
                f"volume '{volume.name}' not found and create=False"
            )

        if volume.datacenter:
            dc = solve_placement(
                specs,
                self._stock,
                volume_name=volume.name,
                existing_dc=volume.datacenter,
            )
        else:
            dc = solve_placement(
                specs, self._stock, volume_name=volume.name
            )

        created = await client.create_network_volume(
            name=volume.name, size=volume.size, data_center_id=dc
        )
        emit(
            self.events,
            "volume_created",
            volume.name,
            volume.size,
            dc,
        )
        log.info(
            "created volume %s (%s, %dGB, %s)",
            volume.name,
            created["id"],
            volume.size,
            dc,
        )
        resolved = {"id": created["id"], "dataCenterId": dc}
        self._resolved[volume.name] = resolved
        return resolved


def specs_sharing_volume(apps: List, name: str) -> List:
    """every resource spec (across apps) attaching the named volume."""
    out = []
    for app in apps:
        for handle in app.resources.values():
            for ref in volume_list(handle.spec.volume):
                if ref.name == name:
                    out.append(handle.spec)
    return out
