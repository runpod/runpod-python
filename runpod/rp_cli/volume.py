"""`rp volume`: network and global volume management."""

import re

import click

from runpod.api.rest import run_rest_request
from runpod.rp_cli import compute, console as ui
from runpod.rp_cli.api import api_errors, list_all
from runpod.rp_cli.output import (
    completer,
    confirm,
    json_option,
    pick,
    print_fields,
    print_json,
    print_table,
)

NETWORK_FIELDS = [
    ("id", lambda row: row["id"]),
    ("name", lambda row: row["name"]),
    ("size", lambda row: f"{row['size']} GB"),
    ("data center", lambda row: row["dataCenter"]),
    ("type", lambda row: row["type"].lower()),
]
GLOBAL_FIELDS = [
    ("id", lambda row: row["id"]),
    ("name", lambda row: row["name"]),
    ("created", lambda row: row["createdAt"]),
    ("created by", lambda row: row["createdByUserId"]),
]
global_option = click.option(
    "--global",
    "kind",
    flag_value="global",
    default="network",
    help="Manage global volumes, which mount in any data center.",
)


def _label(record):
    if "size" not in record:
        return record["name"]
    return (
        f"{record['name']}  {record['size']} GB  "
        f"{record['dataCenter']}  {record['type'].lower()}"
    )


def _choices(kind):
    return [
        (row["id"], _label(row))
        for row in list_all(f"/v2/{kind}-volumes", f"{kind}Volumes")
    ]


def _complete_ids(ctx, param, incomplete):
    return completer(lambda: _choices(ctx.params.get("kind", "network")))(
        ctx, param, incomplete
    )


def _show(record, kind, as_json, action=None):
    if as_json:
        print_json(record)
        return
    if action:
        ui.success(f"{action} {kind} volume [white]{record['id']}[/white]")
    print_fields(record, GLOBAL_FIELDS if kind == "global" else NETWORK_FIELDS)


def _data_center(regions, volume_type):
    datacenters = compute.list_datacenters()
    if regions:
        allowed = compute.resolve_regions(regions, datacenters)
        datacenters = [dc for dc in datacenters if dc["id"] in allowed]
    candidates = [
        dc
        for dc in datacenters
        if dc["networkVolumeTypes"]
        and (not volume_type or volume_type in dc["networkVolumeTypes"])
    ]
    scope = f"region {', '.join(regions)}" if regions else "the catalog"
    offered = (
        f"{volume_type.lower()} network volumes" if volume_type else "network volumes"
    )
    if not candidates:
        raise compute.ResolutionError(f"no data center in {scope} offers {offered}")
    if len(candidates) == 1:
        return candidates[0]["id"]
    return pick(
        "data center",
        [
            (
                dc["id"],
                f"{dc['id']}  {dc['region'].lower().replace('_', ' ')}  "
                + ", ".join(t.lower() for t in dc["networkVolumeTypes"]),
            )
            for dc in candidates
        ],
        f"several data centers in {scope} offer {offered} "
        f"({', '.join(dc['id'] for dc in candidates)}). pass one with --data-center",
    )


def _growth(volume_id, size, expand=False):
    """resolve and validate a size against the current network volume."""
    text = str(size).strip()
    if not re.fullmatch(r"\+?\d+", text):
        raise click.BadParameter(
            f"'{size}' is not a size. use N for a total or +N to add N GB",
            param_hint="--size",
        )
    current = run_rest_request("GET", f"/v2/network-volumes/{volume_id}")["size"]
    target = current + int(text) if text.startswith("+") else int(text)
    if target < current or (expand and target == current):
        raise click.UsageError(
            f"network volumes can only grow: {volume_id} is {current} GB, "
            f"requested {target} GB"
        )
    if target > 4096:
        raise click.UsageError(
            f"network volumes hold at most 4096 GB, requested {target} GB"
        )
    return current, target


@click.group()
def volume():
    """Create, list, resize, and delete network and global volumes."""


@volume.command(name="list")
@global_option
@json_option
def volume_list(kind, as_json):
    """List volumes with their size, data center, and type."""
    with api_errors():
        rows = list_all(f"/v2/{kind}-volumes", f"{kind}Volumes")
    if as_json:
        print_json(rows)
        return
    fields = NETWORK_FIELDS
    if kind == "global":
        fields = GLOBAL_FIELDS[:2] + [("created", lambda row: row["createdAt"][:10])]
    print_table(
        rows,
        [(label.upper(), value) for label, value in fields],
        f"no {kind} volumes. create one with rp volume create",
    )


@volume.command(name="get")
@click.argument("volume_id", required=False, shell_complete=_complete_ids)
@global_option
@json_option
def volume_get(volume_id, kind, as_json):
    """Show one volume (pick one when VOLUME_ID is omitted)."""
    with api_errors():
        if volume_id is None:
            volume_id = pick(f"{kind} volume", _choices(kind), "pass VOLUME_ID")
        record = run_rest_request("GET", f"/v2/{kind}-volumes/{volume_id}")
    _show(record, kind, as_json)


@volume.command(name="create")
@click.option("--name", required=True, help="Human-readable name.")
@click.option(
    "--size",
    type=click.IntRange(10, 4096),
    help="Storage in GB (10-4096). Required for network volumes.",
)
@click.option(
    "--region",
    "regions",
    multiple=True,
    help="Region (eu, na), id prefix (us, eu-ro), or data center id. Repeatable.",
)
@click.option("--data-center", help="Exact data center id, like EU-RO-1.")
@click.option(
    "--type",
    "volume_type",
    type=click.Choice(["standard", "high_performance"], case_sensitive=False),
    help="Storage tier, immutable after creation. Defaults to the data center's "
    "primary tier.",
)
@global_option
@json_option
def volume_create(name, size, regions, data_center, volume_type, kind, as_json):
    """Create a network volume in one data center, or a global volume.

    A region matching several data centers narrows to those offering
    the requested --type, then prompts for one.

    \b
      rp volume create --name models --size 100 --region eu
      rp volume create --name scratch --size 50 --data-center EU-RO-1
      rp volume create --name shared --global
    """
    body = {"name": name}
    with api_errors():
        if kind == "global":
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
                raise click.UsageError(
                    f"{', '.join(given)} do not apply to global volumes"
                )
        else:
            if size is None:
                raise click.UsageError("pass --size in GB for a network volume")
            if regions and data_center:
                raise click.UsageError("use either --region or --data-center")
            volume_type = volume_type.upper() if volume_type else None
            body.update(
                size=size,
                dataCenter=data_center or _data_center(regions, volume_type),
            )
            if volume_type:
                body["type"] = volume_type
        record = run_rest_request("POST", f"/v2/{kind}-volumes", json=body)
    _show(record, kind, as_json, "created")


@volume.command(name="update")
@click.argument("volume_id", shell_complete=_complete_ids)
@click.option("--name", help="New name.")
@click.option(
    "--size",
    type=click.IntRange(10, 4096),
    help="New size in GB; network volumes can only grow.",
)
@global_option
@json_option
def volume_update(volume_id, name, size, kind, as_json):
    """Rename and/or resize a volume."""
    if kind == "global" and size is not None:
        raise click.UsageError("global volumes have no size to change")
    body = {
        key: value
        for key, value in (("name", name), ("size", size))
        if value is not None
    }
    if not body:
        raise click.UsageError("pass --name and/or --size")
    with api_errors():
        if size is not None:
            _growth(volume_id, size)
        record = run_rest_request("PATCH", f"/v2/{kind}-volumes/{volume_id}", json=body)
    _show(record, kind, as_json, "updated")


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
    with api_errors():
        current, target = _growth(volume_id, size, expand=True)
        record = run_rest_request(
            "PATCH", f"/v2/network-volumes/{volume_id}", json={"size": target}
        )
    if as_json:
        print_json(record)
    else:
        ui.success(
            f"expanded [white]{volume_id}[/white] from {current} GB to {record['size']} GB"
        )


@volume.command(name="delete")
@click.argument("volume_id", shell_complete=_complete_ids)
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
@global_option
def volume_delete(volume_id, yes, kind):
    """Delete a volume and everything stored on it."""
    path = f"/v2/{kind}-volumes/{volume_id}"
    with api_errors():
        if not yes:
            record = run_rest_request("GET", path)
            confirm(
                f"delete {kind} volume {volume_id} ({_label(record)}) and all its data?",
                yes,
            )
        run_rest_request("DELETE", path)
    ui.success(f"deleted {kind} volume [white]{volume_id}[/white]")
