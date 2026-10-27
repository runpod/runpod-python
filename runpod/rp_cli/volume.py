import sys
from pathlib import Path

import click


@click.group()
def volume():
    """Upload and download files on network volumes (resumable).

    Uses the S3-compatible API, which needs S3 API keys (separate from your
    API key): set RUNPOD_S3_ACCESS_KEY and RUNPOD_S3_SECRET_KEY, pass the
    --s3-* flags, or enter them when asked. Re-run an interrupted command to resume it. Parts of an upload that is
    never re-run stay in the volume's hidden .s3compat_uploads folder and
    count toward its size.
    """


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

    VOLUME is the volume's id or name. Give only LOCAL_PATH to pick the
    volume from a list. REMOTE_PATH defaults to the local name. A folder's contents go under REMOTE_PATH/.
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

    VOLUME is the volume's id or name. Give only REMOTE_PATH to pick the
    volume from a list. A REMOTE_PATH ending in / is a folder. LOCAL_PATH defaults to the remote
    name. Local files of the same size and not older than the remote copy
    are skipped; --force fetches them anyway.
    """
    reference, remote_path, local_path = _split_args(args, "REMOTE_PATH", "LOCAL_PATH")
    _transfer(ctx, "downloaded", "download", reference, remote_path, local_path, opts)
