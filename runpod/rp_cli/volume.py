import sys
from pathlib import Path

import click


@click.group()
def volume():
    """Upload and download files on network volumes (resumable).

    Uses the S3-compatible API. Set RUNPOD_S3_ACCESS_KEY and
    RUNPOD_S3_SECRET_KEY (create keys at console.runpod.io/user/settings).
    Re-run an interrupted command to resume it. Parts of an upload that is
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


def _volume(reference):
    from runpod.apps import NetworkVolume

    return NetworkVolume(reference, create=False)


@volume.command(name="upload")
@click.argument("volume_ref", metavar="VOLUME")
@click.argument("local_path", type=click.Path(exists=True))
@click.argument("remote_path", required=False)
@_transfer_options
def volume_upload(volume_ref, local_path, remote_path, **opts):
    """Upload a file or folder to a network volume.

    VOLUME is the volume's id or name. REMOTE_PATH defaults to the local
    name. A folder's contents go under REMOTE_PATH/. Files whose remote copy
    has the same size and is not older are skipped; --force sends them
    anyway.
    """
    _run_transfer("uploaded", _volume(volume_ref).upload, local_path, remote_path, opts)


@volume.command(name="download")
@click.argument("volume_ref", metavar="VOLUME")
@click.argument("remote_path")
@click.argument("local_path", required=False)
@_transfer_options
def volume_download(volume_ref, remote_path, local_path, **opts):
    """Download a file or folder from a network volume.

    VOLUME is the volume's id or name. A REMOTE_PATH ending in / is a
    folder. LOCAL_PATH defaults to the remote name. Local files of the same
    size and not older than the remote copy are skipped; --force fetches
    them anyway.
    """
    _run_transfer("downloaded", _volume(volume_ref).download, remote_path, local_path, opts)
