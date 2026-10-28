"""shared pod and serverless log views backed by the sdk stream reader."""

import asyncio
import json
from datetime import datetime, timezone

import aiohttp
import click

from runpod.api.ctl_commands import _path_segment
from runpod.api.rest import run_rest_request
from runpod.apps.logs import stream_pod_logs
from runpod.rp_cli import console as ui
from runpod.rp_cli.api import api_errors

SNAPSHOT_IDLE_SECONDS = 3
WORKER_POLL_SECONDS = 5
RECONNECT_SECONDS = 1


async def _show_logs(
    pod_id, endpoint_id, worker_ids, follow, source, tail, since, as_json
):
    started = datetime.now(timezone.utc)
    records = []
    tasks = {}
    width = 0

    def emit(worker, event):
        if as_json:
            click.echo(
                json.dumps({"worker": worker, **event} if endpoint_id else event)
            )
        else:
            prefix = f"{worker:<{width}} " if endpoint_id else ""
            click.echo(f"{prefix}[{event['source']}] {event['line']}")

    async def consume(worker):
        cursor = None

        def remember(event_id):
            nonlocal cursor
            cursor = event_id

        while True:
            stream = stream_pod_logs(
                worker,
                endpoint_id=endpoint_id,
                log_type=source,
                tail=tail,
                since=since,
                last_event_id=cursor,
                on_event_id=remember,
                idle_timeout=None if follow else SNAPSHOT_IDLE_SECONDS,
            )
            try:
                async for event in stream:
                    if follow:
                        emit(worker, event)
                    elif (
                        datetime.fromisoformat(event["ts"].replace("Z", "+00:00"))
                        >= started
                    ):
                        return
                    else:
                        records.append((worker, event))
            except aiohttp.ClientResponseError as exc:
                if endpoint_id and exc.status == 404:
                    return
                if not follow or not (exc.status == 429 or 500 <= exc.status < 600):
                    raise
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
                if not follow:
                    raise
            finally:
                await stream.aclose()
            if not follow:
                return
            await asyncio.sleep(RECONNECT_SECONDS)

    async def attach():
        nonlocal width
        if pod_id:
            ids = [pod_id]
        else:
            response = await asyncio.to_thread(
                run_rest_request,
                "GET",
                f"/v2/serverless/{_path_segment(endpoint_id)}/workers",
            )
            ids = [
                worker["id"]
                for worker in response["workers"]
                if not worker_ids or worker["id"] in worker_ids
            ]
        width = max([width, *(len(worker) for worker in ids)])
        for worker in ids:
            if worker not in tasks:
                tasks[worker] = asyncio.create_task(consume(worker))

    try:
        await attach()
        if not tasks and not as_json:
            ui.console.print(
                "[dim]waiting for workers ...[/dim]"
                if follow
                else "[dim]no workers[/dim]"
            )
        if not follow:
            await asyncio.gather(*tasks.values())
        else:
            while True:
                await asyncio.sleep(WORKER_POLL_SECONDS)
                for task in tasks.values():
                    if task.done():
                        task.result()
                tasks = {
                    worker: task for worker, task in tasks.items() if not task.done()
                }
                await attach()
    finally:
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
    for worker, event in sorted(records, key=lambda row: row[1]["ts"]):
        emit(worker, event)


def show_logs(
    pod_id=None,
    *,
    endpoint_id=None,
    worker_ids=(),
    follow=False,
    source=None,
    tail=100,
    since=None,
    as_json=False,
):
    try:
        with api_errors():
            asyncio.run(
                _show_logs(
                    pod_id,
                    endpoint_id,
                    worker_ids,
                    follow,
                    source,
                    tail,
                    since,
                    as_json,
                )
            )
    except KeyboardInterrupt:
        return
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise click.ClickException(str(exc)) from exc
