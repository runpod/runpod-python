"""sanitized diagnostics for apps http responses."""

import asyncio
import html
import json
from urllib.parse import quote, quote_plus

import aiohttp
from yarl import URL

from ..errors import AppHTTPError, JobStatusError

BODY_READ_LIMIT = 65536
BODY_EXCERPT_LIMIT = 1024


async def raise_for_status(
    response: aiohttp.ClientResponse, *, job_id: str | None = None
) -> None:
    if response.status < 400:
        return

    secrets = set()
    for item in (*response.history, response):
        url = item.request_info.real_url
        secrets.update(value for value in url.query.values() if value)
        secrets.update(value for value in (url.user, url.password) if value)
        for name, value in item.request_info.headers.items():
            if any(
                word in name.lower()
                for word in ("authorization", "cookie", "key", "token", "secret")
            ):
                secrets.add(value)
                if name.lower() in ("authorization", "proxy-authorization"):
                    secrets.add(value.partition(" ")[2])
                if name.lower() == "cookie":
                    secrets.update(
                        part.partition("=")[2].strip() for part in value.split(";")
                    )

    sensitive = set()
    for value in secrets:
        if value:
            sensitive.update(
                (
                    value,
                    quote(value, safe=""),
                    quote_plus(value),
                    html.escape(value),
                    json.dumps(value)[1:-1],
                )
            )

    try:
        try:
            data = await response.content.readexactly(BODY_READ_LIMIT + 1)
        except asyncio.IncompleteReadError as exc:
            data = exc.partial
        body = data.decode("utf-8", errors="replace")
    except (aiohttp.ClientError, asyncio.TimeoutError):
        body = "[response body unavailable]"

    truncated = len(body) > BODY_EXCERPT_LIMIT
    body = body[:BODY_EXCERPT_LIMIT]
    url = str(URL(response.url).with_user(None).with_query(None).with_fragment(None))
    for value in sorted(sensitive, key=len, reverse=True):
        url = url.replace(value, "[redacted]")
        body = body.replace(value, "[redacted]")
        if truncated:
            for length in range(min(len(value) - 1, len(body)), 0, -1):
                if body.endswith(value[:length]):
                    body = body[:-length] + "[redacted]"
                    break
    body = body[:BODY_EXCERPT_LIMIT]
    if truncated:
        body = body[: BODY_EXCERPT_LIMIT - 3] + "..."

    if job_id is not None:
        raise JobStatusError(
            job_id, response.status, response.method, url, body
        ) from None
    raise AppHTTPError(response.method, url, response.status, body) from None
