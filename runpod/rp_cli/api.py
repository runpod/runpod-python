"""pagination and error reporting for rest-backed cli commands."""

from contextlib import contextmanager

import click
from requests import RequestException

from runpod import error
from runpod.api.rest import run_rest_request
from runpod.rp_cli.compute import ResolutionError


def list_all(path: str, collection: str, params: dict | None = None) -> list[dict]:
    query = dict(params or {})
    records = []
    while True:
        page = run_rest_request("GET", path, params=query)
        records.extend(page[collection])
        pagination = page["pagination"]
        if not pagination["hasNextPage"]:
            return records
        query["cursor"] = pagination["nextCursor"]


@contextmanager
def api_errors():
    try:
        yield
    except (error.RunPodError, ResolutionError, RequestException) as exc:
        raise click.ClickException(str(exc)) from exc
