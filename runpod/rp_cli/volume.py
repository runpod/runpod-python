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


def _run_transfer(direction, reference, source, target, opts) -> None:
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        TextColumn,
        TransferSpeedColumn,
    )

    from runpod import network_volume_transfer as nvt
    from runpod.apps import NetworkVolume

    tasks = {}
    with Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        console=Console(stderr=True),
        transient=True,
    ) as bar:

        def progress(event, path, nbytes):
            if event == "start":
                tasks[path] = bar.add_task(Path(path).name, total=nbytes)
            elif event in ("advance", "reuse") and path in tasks:
                bar.advance(tasks[path], nbytes)
            elif event == "done" and path in tasks:
                bar.remove_task(tasks.pop(path))
                bar.console.print(f"  {direction} {path}")

        target_volume = NetworkVolume(reference, create=False)
        func = target_volume.upload if direction == "uploaded" else target_volume.download
        try:
            summary = func(
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

    click.echo(
        f"{direction} {summary['files_transferred']} file(s), "
        f"{summary['bytes_transferred']} bytes; "
        f"skipped {summary['files_skipped']} unchanged"
    )


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
    _run_transfer("uploaded", volume_ref, local_path, remote_path, opts)


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
    _run_transfer("downloaded", volume_ref, remote_path, local_path, opts)
