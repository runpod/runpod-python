import re
import sys
from pathlib import Path

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
    """Create, list, resize, and delete volumes, and transfer files to them.

    upload and download use the S3-compatible API, which needs S3 API keys
    (separate from your API key): set RUNPOD_S3_ACCESS_KEY and
    RUNPOD_S3_SECRET_KEY, pass the --s3-* flags, or enter them when asked.
    Re-run an interrupted transfer to resume it. Parts of an upload that is
    never re-run stay in the volume's hidden .s3compat_uploads folder and
    count toward its size.
    """


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


def _transfer_options(func):
    options = [
        click.option("--s3-access-key", default=None, help="S3 API access key."),
        click.option("--s3-secret-key", default=None, help="S3 API secret key."),
        click.option("--endpoint-url", default=None, hidden=True),
        click.option(
            "--concurrency", default=4, show_default=True, type=click.IntRange(1, 64),
            help="Parallel requests.",
        ),
        click.option(
            "--force", is_flag=True,
            help="Transfer every file, even ones the size and time check calls unchanged.",
        ),
    ]
    for option in reversed(options):
        func = option(func)
    return func


class _ProgressRenderer:
    """draws one bar per file in flight and prints each finished file."""

    def __init__(self, bar, action_label):
        self._bar = bar
        self._action_label = action_label
        self._tasks = {}

    def __call__(self, event, path, nbytes):
        if event == "start":
            self._tasks[path] = self._bar.add_task(Path(path).name, total=nbytes)
            return
        task = self._tasks.get(path)
        if task is None:
            return
        if event in ("advance", "reuse"):
            self._bar.advance(task, nbytes)
        elif event == "done":
            self._bar.remove_task(self._tasks.pop(path))
            self._bar.console.print(f"  {self._action_label} {path}")


def _progress_bar():
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        TextColumn,
        TransferSpeedColumn,
    )

    return Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        console=Console(stderr=True),
        transient=True,
    )


def _invoke_transfer(transfer_method, source, target, opts, progress):
    """run the transfer, turning failures into cli errors with a resume hint
    when re-running would resume."""
    from runpod import network_volume_transfer as nvt

    try:
        return transfer_method(
            source,
            target,
            access_key=opts["s3_access_key"],
            secret_key=opts["s3_secret_key"],
            endpoint_url=opts["endpoint_url"],
            concurrency=opts["concurrency"],
            progress=progress,
            force=opts["force"],
        )
    except KeyboardInterrupt:
        click.echo("interrupted; re-run the same command to resume", err=True)
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001 - surface transfer errors cleanly
        hint = "\nre-run the same command to resume" if nvt.is_resumable(exc) else ""
        raise click.ClickException(f"{exc}{hint}") from exc


def _run_transfer(action_label, transfer_method, source, target, opts) -> None:
    with _progress_bar() as bar:
        summary = _invoke_transfer(
            transfer_method, source, target, opts, _ProgressRenderer(bar, action_label)
        )
    click.echo(
        f"{action_label} {summary['files_transferred']} file(s), "
        f"{summary['bytes_transferred']} bytes; "
        f"skipped {summary['files_skipped']} unchanged"
    )


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _ensure_logged_in(ctx) -> None:
    """offer the browser login when no api key is set; the volume lookup
    needs one."""
    import runpod
    from runpod import network_volume_transfer as nvt
    from runpod.cli.groups.config.functions import get_credentials
    from runpod.rp_cli.main import login

    if runpod.api_key:
        return
    if not _interactive() or not click.confirm(
        "you are not logged in to runpod. log in now?", default=True
    ):
        raise click.ClickException(nvt.NOT_LOGGED_IN)
    ctx.invoke(login, no_open=False, api_key_opt=None, profile=runpod.profile)
    runpod.api_key = (get_credentials(runpod.profile) or {}).get("api_key")
    if not runpod.api_key:
        raise click.ClickException(nvt.NOT_LOGGED_IN)


def _pick_volume() -> str:
    """let the user choose one of their network volumes by number."""
    from runpod import network_volume_transfer as nvt

    if not _interactive():
        raise click.UsageError("pass VOLUME first (a network volume id or name)")
    try:
        volumes = nvt._list_volumes()
    except nvt.NetworkVolumeTransferError as exc:
        raise click.ClickException(str(exc)) from exc
    if not volumes:
        raise click.ClickException(
            "no network volumes yet: create one at https://console.runpod.io/user/storage"
        )
    for number, vol in enumerate(volumes, 1):
        datacenter = vol.get("dataCenterId") or vol.get("dataCenter")
        click.echo(
            f"  {number}. {vol['name']}  {vol['id']}  {vol.get('size')} GB  {datacenter}"
        )
    choice = click.prompt("volume", type=click.IntRange(1, len(volumes)))
    return volumes[choice - 1]["id"]


def _missing_keys_message() -> str:
    from runpod import network_volume_transfer as nvt

    return (
        "s3 api keys required (separate from your api key): set "
        f"{nvt.ACCESS_KEY_ENV} and {nvt.SECRET_KEY_ENV} or pass --s3-access-key "
        f"and --s3-secret-key (create keys at {nvt.S3_KEYS_URL})"
    )


def _prompt_s3_keys():
    """ask for the s3 keys, offering to save them for next time."""
    import runpod
    from runpod import network_volume_transfer as nvt
    from runpod.cli.groups.config.functions import set_s3_keys

    if not _interactive():
        raise click.ClickException(_missing_keys_message())
    click.echo(
        "network volume transfers use s3 api keys, separate from your api key.\n"
        f"create a pair at {nvt.S3_KEYS_URL} (s3 api keys)."
    )
    access = click.prompt("s3 access key")
    secret = click.prompt("s3 secret key", hide_input=True)
    if click.confirm("save them to ~/.runpod/config.toml?", default=True):
        set_s3_keys(access, secret, profile=runpod.profile)
    return access, secret


def _ensure_s3_keys(opts) -> None:
    """fill in the s3 keys from the flags, the environment, or the saved
    config, prompting for them when none are set."""
    from runpod import network_volume_transfer as nvt

    keys = nvt.find_s3_keys(opts["s3_access_key"], opts["s3_secret_key"])
    opts["s3_access_key"], opts["s3_secret_key"] = keys or _prompt_s3_keys()


def _split_args(args, second: str, third: str):
    """[VOLUME] SECOND [THIRD]: a lone argument is SECOND, and the volume is
    picked interactively."""
    if not 1 <= len(args) <= 3:
        raise click.UsageError(f"expected [VOLUME] {second} [{third}]")
    if len(args) == 1:
        return None, args[0], None
    return args[0], args[1], args[2] if len(args) == 3 else None


def _transfer(ctx, action_label, method_name, reference, source, target, opts) -> None:
    """the checks every transfer needs, in order: login, volume, s3 keys."""
    from runpod.apps import NetworkVolume

    _ensure_logged_in(ctx)
    reference = reference or _pick_volume()
    _ensure_s3_keys(opts)
    method = getattr(NetworkVolume(reference, create=False), method_name)
    _run_transfer(action_label, method, source, target, opts)


@volume.command(name="upload")
@click.argument("args", nargs=-1, required=True, metavar="[VOLUME] LOCAL_PATH [REMOTE_PATH]")
@_transfer_options
@click.pass_context
def volume_upload(ctx, args, **opts):
    """Upload a file or folder to a network volume.

    VOLUME is the volume's id or name; leave it out to pick one. REMOTE_PATH
    defaults to the local name. A folder's contents go under REMOTE_PATH/.
    Files whose remote copy has the same size and is not older are skipped;
    --force sends them anyway.
    """
    reference, local_path, remote_path = _split_args(args, "LOCAL_PATH", "REMOTE_PATH")
    if not Path(local_path).exists():
        raise click.UsageError(f"local path {local_path} does not exist")
    _transfer(ctx, "uploaded", "upload", reference, local_path, remote_path, opts)


@volume.command(name="download")
@click.argument("args", nargs=-1, required=True, metavar="[VOLUME] REMOTE_PATH [LOCAL_PATH]")
@_transfer_options
@click.pass_context
def volume_download(ctx, args, **opts):
    """Download a file or folder from a network volume.

    VOLUME is the volume's id or name; leave it out to pick one. A
    REMOTE_PATH ending in / is a folder. LOCAL_PATH defaults to the remote
    name. Local files of the same size and not older than the remote copy
    are skipped; --force fetches them anyway.
    """
    reference, remote_path, local_path = _split_args(args, "REMOTE_PATH", "LOCAL_PATH")
    _transfer(ctx, "downloaded", "download", reference, remote_path, local_path, opts)
