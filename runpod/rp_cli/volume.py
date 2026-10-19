"""`rp volume`: network and global volume management."""

import re
from typing import NamedTuple, Optional

import click

from runpod.api.rest import run_rest_request
from runpod.rp_cli import compute
from runpod.rp_cli.output import (
    api_errors,
    completer,
    confirm,
    json_option,
    pick,
    print_fields,
    print_json,
    print_table,
)

MIN_SIZE = 10
MAX_SIZE = 4096


class Kind(NamedTuple):
    path: str
    collection: str
    noun: str


NETWORK = Kind("/v2/network-volumes", "networkVolumes", "network volume")
GLOBAL = Kind("/v2/global-volumes", "globalVolumes", "global volume")


def _kind(global_volume: bool) -> Kind:
    return GLOBAL if global_volume else NETWORK


def _type_label(record: dict) -> Optional[str]:
    return (record.get("type") or "").lower() or None


def _size_label(record: dict) -> Optional[str]:
    return f"{record['size']} GB" if record.get("size") else None


def _columns(kind: Kind):
    if kind is GLOBAL:
        return [
            ("ID", lambda row: row["id"]),
            ("NAME", lambda row: row.get("name")),
            ("CREATED", lambda row: row.get("createdAt")),
            ("CREATED BY", lambda row: row.get("createdByUserId")),
        ]
    return [
        ("ID", lambda row: row["id"]),
        ("NAME", lambda row: row.get("name")),
        ("SIZE", _size_label),
        ("DATA CENTER", lambda row: row.get("dataCenter")),
        ("TYPE", _type_label),
    ]


def _list(kind: Kind) -> list[dict]:
    return compute.list_all(kind.path, kind.collection)


def _get(kind: Kind, volume_id: str) -> dict:
    return run_rest_request("GET", f"{kind.path}/{volume_id}") or {}


def _label(kind: Kind, record: dict) -> str:
    if kind is GLOBAL:
        return record.get("name") or ""
    details = [_size_label(record), record.get("dataCenter"), _type_label(record)]
    return "  ".join([record.get("name") or ""] + [d for d in details if d])


def _choices(kind: Kind) -> list[tuple[str, str]]:
    return [(record["id"], _label(kind, record)) for record in _list(kind)]


def _complete_ids(ctx, param, incomplete):
    kind = _kind(bool(ctx.params.get("global_volume")))
    return completer(lambda: _choices(kind))(ctx, param, incomplete)


def _show(kind: Kind, record: dict, as_json: bool) -> None:
    if as_json:
        print_json(record)
        return
    print_fields(record, [(header.lower(), fn) for header, fn in _columns(kind)])


def _data_center(
    regions: tuple[str, ...], data_center: Optional[str], volume_type: Optional[str]
) -> str:
    """the single data center a network volume is created in.

    regions narrow to data centers offering the requested volume type
    (or any type), then a person picks one; scripts get the candidates.
    """
    if data_center:
        return data_center
    datacenters = compute.list_datacenters()
    if regions:
        allowed = set(compute.resolve_regions(regions, datacenters))
        datacenters = [dc for dc in datacenters if dc["id"] in allowed]
    candidates = [
        dc
        for dc in datacenters
        if (volume_type in (dc.get("networkVolumeTypes") or []))
        or (not volume_type and dc.get("networkVolumeTypes"))
    ]
    scope = f"region {', '.join(regions)}" if regions else "the catalog"
    offered = (
        f"{volume_type.lower()} network volumes" if volume_type else "network volumes"
    )
    if not candidates:
        raise compute.ResolutionError(f"no data center in {scope} offers {offered}")
    if len(candidates) == 1:
        return candidates[0]["id"]
    ids = ", ".join(dc["id"] for dc in candidates)
    return pick(
        "data center",
        [
            (
                dc["id"],
                f"{dc['id']}  {(dc.get('region') or '').lower().replace('_', ' ')}  "
                + ", ".join(t.lower() for t in dc["networkVolumeTypes"]),
            )
            for dc in candidates
        ],
        f"several data centers in {scope} offer {offered} ({ids}). "
        "pass one with --data-center",
    )


def _parse_growth(value: str) -> tuple[bool, int]:
    """split an expand size into (relative, gigabytes): N or +N."""
    text = value.strip()
    if not re.fullmatch(r"\+?\d+", text):
        raise click.BadParameter(
            f"'{value}' is not a size. use N for a total or +N to add N GB",
            param_hint="--size",
        )
    return text.startswith("+"), int(text.lstrip("+"))


def _check_growth(volume_id: str, current: int, size: int, strict: bool) -> None:
    if size < current or (strict and size == current):
        raise click.UsageError(
            f"network volumes can only grow: {volume_id} is {current} GB, "
            f"requested {size} GB"
        )
    if size > MAX_SIZE:
        raise click.UsageError(
            f"network volumes hold at most {MAX_SIZE} GB, requested {size} GB"
        )


global_option = click.option(
    "--global",
    "global_volume",
    is_flag=True,
    help="Manage global volumes, which mount in any data center.",
)


@click.group()
def volume():
    """Create, list, resize, and delete network and global volumes."""


@volume.command(name="list")
@global_option
@json_option
def volume_list(global_volume, as_json):
    """List volumes with their size, data center, and type."""
    kind = _kind(global_volume)
    with api_errors():
        rows = _list(kind)
    if as_json:
        print_json(rows)
        return
    print_table(
        rows, _columns(kind), f"no {kind.noun}s. create one with rp volume create"
    )


@volume.command(name="get")
@click.argument("volume_id", required=False, shell_complete=_complete_ids)
@global_option
@json_option
def volume_get(volume_id, global_volume, as_json):
    """Show one volume (pick one when VOLUME_ID is omitted)."""
    kind = _kind(global_volume)
    with api_errors():
        if volume_id is None:
            volume_id = pick(kind.noun, _choices(kind), "pass VOLUME_ID")
        record = _get(kind, volume_id)
    _show(kind, record, as_json)


@volume.command(name="create")
@click.option("--name", required=True, help="Human-readable name.")
@click.option(
    "--size",
    type=click.IntRange(MIN_SIZE, MAX_SIZE),
    default=None,
    help=f"Storage in GB ({MIN_SIZE}-{MAX_SIZE}). Required for network volumes.",
)
@click.option(
    "--region",
    "regions",
    multiple=True,
    help="Region (eu, na), id prefix (us, eu-ro), or data center id. Repeatable.",
)
@click.option("--data-center", default=None, help="Exact data center id, like EU-RO-1.")
@click.option(
    "--type",
    "volume_type",
    type=click.Choice(["standard", "high_performance"], case_sensitive=False),
    default=None,
    help="Storage tier, immutable after creation. Defaults to the data center's "
    "primary tier.",
)
@global_option
@json_option
def volume_create(
    name, size, regions, data_center, volume_type, global_volume, as_json
):  # pylint: disable=too-many-arguments
    """Create a network volume in one data center, or a global volume.

    A region matching several data centers narrows to those offering
    the requested --type, then prompts for one.

    \b
      rp volume create --name models --size 100 --region eu
      rp volume create --name scratch --size 50 --data-center EU-RO-1
      rp volume create --name shared --global
    """
    from runpod.rp_cli import console as ui

    kind = _kind(global_volume)
    if global_volume:
        given = [
            flag
            for flag, value in (
                ("--size", size),
                ("--region", regions),
                ("--data-center", data_center),
                ("--type", volume_type),
            )
            if value
        ]
        if given:
            raise click.UsageError(f"{', '.join(given)} do not apply to global volumes")
        body = {"name": name}
    else:
        if size is None:
            raise click.UsageError("pass --size in GB for a network volume")
        if regions and data_center:
            raise click.UsageError("use either --region or --data-center")
        volume_type = volume_type.upper() if volume_type else None
        with api_errors():
            body = {
                "name": name,
                "size": size,
                "dataCenter": _data_center(regions, data_center, volume_type),
            }
        if volume_type:
            body["type"] = volume_type

    with api_errors():
        record = run_rest_request("POST", kind.path, json=body) or {}
    if as_json:
        print_json(record)
        return
    ui.success(f"created {kind.noun} [white]{record.get('id')}[/white]")
    _show(kind, record, as_json)


@volume.command(name="update")
@click.argument("volume_id", shell_complete=_complete_ids)
@click.option("--name", default=None, help="New name.")
@click.option(
    "--size",
    type=click.IntRange(MIN_SIZE, MAX_SIZE),
    default=None,
    help="New size in GB; network volumes can only grow.",
)
@global_option
@json_option
def volume_update(volume_id, name, size, global_volume, as_json):
    """Rename and/or resize a volume."""
    from runpod.rp_cli import console as ui

    kind = _kind(global_volume)
    if global_volume and size is not None:
        raise click.UsageError("global volumes have no size to change")
    if name is None and size is None:
        raise click.UsageError("pass --name and/or --size")
    body = {
        key: value
        for key, value in (("name", name), ("size", size))
        if value is not None
    }
    with api_errors():
        if size is not None:
            current = _get(kind, volume_id).get("size") or 0
            _check_growth(volume_id, current, size, strict=False)
        record = run_rest_request("PATCH", f"{kind.path}/{volume_id}", json=body) or {}
    if as_json:
        print_json(record)
        return
    ui.success(f"updated {kind.noun} [white]{volume_id}[/white]")
    _show(kind, record, as_json)


@volume.command(name="expand")
@click.argument("volume_id", shell_complete=_complete_ids)
@click.option(
    "--size",
    required=True,
    metavar="N|+N",
    help="New total size in GB, or +N to add N GB.",
)
@json_option
def volume_expand(volume_id, size, as_json):
    """Grow a network volume; storage can never shrink."""
    from runpod.rp_cli import console as ui

    relative, amount = _parse_growth(size)
    with api_errors():
        current = _get(NETWORK, volume_id).get("size") or 0
        target = current + amount if relative else amount
        _check_growth(volume_id, current, target, strict=True)
        record = (
            run_rest_request(
                "PATCH", f"{NETWORK.path}/{volume_id}", json={"size": target}
            )
            or {}
        )
    if as_json:
        print_json(record)
        return
    ui.success(
        f"expanded [white]{volume_id}[/white] from {current} GB to "
        f"{record.get('size', target)} GB"
    )


@volume.command(name="delete")
@click.argument("volume_id", shell_complete=_complete_ids)
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
@global_option
def volume_delete(volume_id, yes, global_volume):
    """Delete a volume and everything stored on it."""
    from runpod.rp_cli import console as ui

    kind = _kind(global_volume)
    with api_errors():
        if not yes:
            record = _get(kind, volume_id)
            confirm(
                f"delete {kind.noun} {volume_id} ({_label(kind, record)}) "
                "and all its data?",
                yes,
            )
        run_rest_request("DELETE", f"{kind.path}/{volume_id}")
    ui.success(f"deleted {kind.noun} [white]{volume_id}[/white]")
