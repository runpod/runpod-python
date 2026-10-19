import contextlib
import fnmatch
import json
import time
from typing import Any, Iterator, Optional
from urllib.parse import quote

import click
import requests

from rich.markup import escape

from runpod.api.rest import run_rest_request
from runpod.apps.utils.network import api_key
from runpod.endpoint import runner
from runpod.endpoint.helpers import is_completed
from runpod.rp_cli import compute, console as ui, endpoint_config as config
from runpod.rp_cli.api import api_errors, list_all
from runpod.rp_cli.output import (
    completer,
    confirm,
    interactive,
    json_option,
    pick,
    print_fields,
    print_json,
    print_table,
    table,
)

TYPES = {"queue": "QUEUE", "load-balancer": "LOAD_BALANCER"}


def _path(endpoint_id: str, *parts: str) -> str:
    segments = [quote(value, safe="") for value in (endpoint_id, *parts)]
    return "/v2/serverless/" + "/".join(segments)


def _choices(path: str, collection: str):
    def fetch():
        return [
            (record["id"], record.get("name") or "")
            for record in list_all(path, collection)
        ]

    return completer(fetch)


complete_endpoint = _choices("/v2/serverless", "endpoints")
complete_network_volume = _choices("/v2/network-volumes", "networkVolumes")
complete_global_volume = _choices("/v2/global-volumes", "globalVolumes")
complete_registry = _choices("/v2/registries", "registries")
complete_template = _choices("/v2/templates", "templates")
complete_datacenter = completer(
    lambda: [
        (dc["id"], (dc.get("region") or "").lower())
        for dc in compute.list_datacenters()
    ]
)


def complete_worker(ctx, param, incomplete):
    endpoint_id = ctx.params.get("endpoint_id")
    if not endpoint_id:
        return []
    return completer(
        lambda: [
            (worker["id"], (worker.get("status") or "").lower())
            for worker in run_rest_request("GET", _path(endpoint_id, "workers"))[
                "workers"
            ]
        ]
    )(ctx, param, incomplete)


def endpoint_argument(required: bool = True):
    return click.argument(
        "endpoint_id", required=required, shell_complete=complete_endpoint
    )


def _pick_endpoint(endpoint_id: Optional[str]) -> str:
    if endpoint_id:
        return endpoint_id
    if not interactive():
        raise click.UsageError("pass an ENDPOINT_ID")
    with api_errors():
        endpoints = list_all("/v2/serverless", "endpoints")
    return pick(
        "endpoint",
        [
            (e["id"], f"{e['id']}  {e.get('name') or ''}  {_type(e) or ''}")
            for e in endpoints
        ],
        "pass an ENDPOINT_ID",
    )


def _type(endpoint: dict) -> Optional[str]:
    names = {value: key for key, value in TYPES.items()}
    return names.get(endpoint.get("type"), endpoint.get("type"))


def _flashboot(endpoint: dict) -> Optional[str]:
    names = {value: key for key, value in config.FLASHBOOT.items()}
    return names.get(endpoint.get("flashboot"), endpoint.get("flashboot"))


def _compute(endpoint: dict, compact: bool = False) -> Optional[str]:
    gpu = endpoint.get("gpu")
    if gpu:
        text = f"{', '.join(gpu.get('pools') or [])} x{gpu.get('count') or 1}"
        excluded = gpu.get("excludedTypes") or []
        if compact or not excluded:
            return text
        return f"{text} (excluding {', '.join(excluded)})"
    cpus = endpoint.get("cpu") or []
    return ", ".join(f"{cpu['id']} {cpu.get('vcpuCount')} vcpu" for cpu in cpus) or None


def _cuda(endpoint: dict) -> Optional[str]:
    gpu = endpoint.get("gpu")
    if not gpu:
        return None
    if gpu.get("minCudaVersion"):
        return f">={gpu['minCudaVersion']}"
    return ", ".join(gpu.get("allowedCudaVersions") or []) or "any"


def _scaling(endpoint: dict, compact: bool = False) -> Optional[str]:
    scaling = endpoint.get("scaling") or {}
    if scaling.get("type") == "QUEUE_DELAY":
        delay = f"{scaling.get('queueDelay'):g}s"
        return f"{delay} delay" if compact else f"queue delay {delay}"
    if scaling.get("type") == "REQUEST_COUNT":
        count = scaling.get("requestCount")
        return f"{count}/worker" if compact else f"{count} requests per worker"
    return None


def _workers(endpoint: dict) -> Optional[str]:
    workers = endpoint.get("workers") or {}
    if "min" not in workers and "max" not in workers:
        return None
    return f"{workers.get('min', 0)}-{workers.get('max', 0)}"


def _seconds(value: Optional[float]) -> Optional[str]:
    return f"{value:g}s" if value else None


def _volumes(endpoint: dict) -> list[str]:
    mounts = endpoint.get("mounts") or {}
    return [
        f"{mount['volumeId']} ({kind})"
        for kind in ("network", "global")
        for mount in mounts.get(kind) or []
    ]


def _url(endpoint: dict) -> Optional[str]:
    urls = endpoint.get("requestUrls") or {}
    return urls.get("base") or urls.get("run")


_LIST_COLUMNS = [
    ("ID", lambda e: e["id"]),
    ("NAME", lambda e: e.get("name")),
    ("TYPE", _type),
    ("COMPUTE", lambda e: _compute(e, compact=True)),
    ("WORKERS", _workers),
    ("SCALING", lambda e: _scaling(e, compact=True)),
    ("CREATED", lambda e: (e.get("createdAt") or "")[:10]),
]

_FIELDS = [
    ("id", lambda e: e.get("id")),
    ("name", lambda e: e.get("name")),
    ("type", _type),
    ("image", lambda e: e.get("image")),
    ("compute", _compute),
    ("cuda", _cuda),
    ("workers", _workers),
    ("idle timeout", lambda e: _seconds((e.get("workers") or {}).get("idleTimeout"))),
    ("scaling", _scaling),
    ("request timeout", lambda e: _seconds((e.get("timeout") or 0) / 1000)),
    ("flashboot", _flashboot),
    ("data centers", lambda e: e.get("dataCenterIds")),
    ("volumes", _volumes),
    ("env", lambda e: sorted(e.get("env") or {})),
    ("ports", lambda e: e.get("ports")),
    ("disk", lambda e: f"{e['disk']}GB" if e.get("disk") else None),
    ("args", lambda e: e.get("args")),
    ("registry", lambda e: e.get("registry")),
    ("url", _url),
    ("created", lambda e: e.get("createdAt")),
]


def _scale_options(func):
    for option in reversed(
        [
            click.option(
                "--min-workers", type=click.IntRange(min=0), help="Minimum workers."
            ),
            click.option(
                "--max-workers", type=click.IntRange(min=0), help="Maximum workers."
            ),
            click.option(
                "--idle-timeout-seconds",
                type=click.IntRange(1, 3600),
                help="Seconds before idle workers scale down.",
            ),
            click.option(
                "--queue-delay-seconds",
                type=click.FloatRange(min=0.5),
                help="Scale on queue wait time (queue endpoints).",
            ),
            click.option(
                "--requests-per-worker",
                type=click.IntRange(min=1),
                help="Scale on in-flight requests per worker.",
            ),
        ]
    ):
        func = option(func)
    return func


def _config_options(creating: bool):
    head = [click.option("--name", required=creating, help="Endpoint name.")]
    if creating:
        head.append(
            click.option(
                "--type",
                "endpoint_type",
                required=True,
                type=click.Choice(list(TYPES), case_sensitive=False),
                callback=lambda ctx, param, value: (
                    TYPES[value.lower()] if value else value
                ),
                help="Request routing: queued jobs or load-balanced HTTP.",
            )
        )
    options = head + [
        click.option("--image", help="Container image."),
        click.option(
            "--template",
            help="Serverless template ID to base the container on.",
            shell_complete=complete_template,
        ),
        click.option(
            "--gpu",
            multiple=True,
            help="GPU type (4090, h100, a100-sxm4) or pool id, optionally with a "
            "count (h100:2). Repeatable.",
        ),
        click.option(
            "--gpu-count", type=click.IntRange(min=1), help="GPUs per worker."
        ),
        click.option(
            "--cuda",
            multiple=True,
            help="CUDA constraint: '>=12.4' for a floor, 12.4 or 12.4,12.8 for exact "
            "versions, any for no constraint.",
        ),
        click.option(
            "--cpu",
            multiple=True,
            help="CPU flavor (cpu3c, cpu5g), optionally with vCPUs (cpu3c:8). Repeatable.",
        ),
        click.option(
            "--vcpu-count", type=click.IntRange(min=2), help="vCPUs per worker."
        ),
        _scale_options,
        click.option(
            "--request-timeout-seconds",
            type=click.IntRange(min=0),
            help="Per-request execution timeout.",
        ),
        click.option(
            "--data-center",
            multiple=True,
            help="Data center id (EU-RO-1). Repeatable.",
            shell_complete=complete_datacenter,
        ),
        click.option(
            "--region",
            multiple=True,
            help="Region (eu, na, asia) or id prefix (us, eu-ro). Repeatable.",
        ),
        click.option(
            "--network-volume",
            multiple=True,
            help="Network volume ID, at most one per data center. Repeatable.",
            shell_complete=complete_network_volume,
        ),
        click.option(
            "--global-volume",
            multiple=True,
            help="Global volume ID. Repeatable.",
            shell_complete=complete_global_volume,
        ),
        click.option(
            "--env", multiple=True, help="Environment variable KEY=VALUE. Repeatable."
        ),
        click.option(
            "--flashboot",
            type=click.Choice(list(config.FLASHBOOT), case_sensitive=False),
            help="FlashBoot mode.",
        ),
        click.option(
            "--disk", type=click.IntRange(min=1), help="Container disk in GB."
        ),
        click.option(
            "--port", multiple=True, help="Exposed port as port/protocol. Repeatable."
        ),
        click.option("--args", help="Container start command as one raw string."),
        click.option("--entrypoint", help="Container ENTRYPOINT, shell-quoted."),
        click.option("--cmd", help="Container CMD, shell-quoted."),
        click.option(
            "--registry",
            help="Registry credential ID for private images.",
            shell_complete=complete_registry,
        ),
    ]
    if not creating:
        options.append(
            click.option(
                "--unset-env",
                multiple=True,
                help="Remove an environment variable. Repeatable.",
            )
        )

    def decorate(func):
        for option in reversed(options):
            func = option(func)
        return func

    return decorate


def _rollout_line(state: dict) -> str:
    rollout = state.get("rollout") or {}
    on_latest = f"{rollout.get('workersOnLatest', 0)}/{rollout.get('workersTotal', 0)}"
    phase = "rolling out" if rollout.get("inProgress") else "live"
    return f"v{state.get('endpointVersion')} {phase}, {on_latest} workers on latest"


def _follow_rollout(
    endpoint_id: str,
    previous_version: Optional[int],
    timeout: float,
    interval: float = 2.0,
) -> None:
    started = time.monotonic()
    last = None
    try:
        while True:
            with api_errors():
                state = run_rest_request(
                    "GET", _path(endpoint_id, "releases"), params={"limit": 1}
                )
            version = state.get("endpointVersion")
            elapsed = time.monotonic() - started
            if previous_version is not None and version == previous_version:
                if elapsed >= min(10.0, timeout):
                    ui.console.print("[dim]no new release to roll out[/dim]")
                    return
            else:
                line = _rollout_line(state)
                if line != last:
                    ui.console.print(f"[dim]{line}[/dim]")
                    last = line
                if not (state.get("rollout") or {}).get("inProgress"):
                    ui.success(f"release v{version} is live")
                    return
            if elapsed >= timeout:
                ui.warn(
                    f"rollout still in progress after {timeout:g}s. "
                    f"follow with: rp endpoint releases {endpoint_id}"
                )
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        ui.console.print(
            "\n[dim]detached; the rollout continues. "
            f"follow with: rp endpoint releases {endpoint_id}[/dim]"
        )


def _apply(
    endpoint_id: str, patch: dict, follow: bool, timeout: float, as_json: bool
) -> None:
    with api_errors():
        before = (
            run_rest_request(
                "GET", _path(endpoint_id, "releases"), params={"limit": 1}
            ).get("endpointVersion")
            if follow
            else None
        )
        updated = run_rest_request("PATCH", _path(endpoint_id), json=patch)
    if as_json:
        print_json(updated)
    else:
        ui.success(
            f"updated [white]{escape(updated['name'])}[/white] "
            f"[dim]{endpoint_id}[/dim]"
        )
    if follow:
        _follow_rollout(endpoint_id, before, timeout)


def _edit_document(before: dict) -> Optional[dict]:
    text = json.dumps(before, indent=2) + "\n"
    while True:
        edited = click.edit(text, extension=".json")
        if edited is None:
            return None
        try:
            return config.validate(edited, before)
        except click.ClickException as exc:
            ui.error(escape(exc.message))
            if not click.confirm("re-open the editor?", default=True):
                raise
            text = edited


def _uptime(seconds: Optional[int]) -> Optional[str]:
    if not seconds:
        return None
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m{secs:02d}s" if minutes else f"{secs}s"


def _worker_version(worker: dict) -> Optional[str]:
    stale = " (stale)" if worker.get("isStale") else ""
    if worker.get("version") is None:
        return stale.strip(" ()") or None
    return f"v{worker['version']}{stale}"


_WORKER_COLUMNS = [
    ("WORKER", lambda w: w["id"]),
    ("STATUS", lambda w: (w.get("status") or "").lower()),
    (
        "GPU",
        lambda w: (
            f"{w.get('gpuTypeId')} x{w['gpuCount']}" if w.get("gpuCount") else None
        ),
    ),
    ("DATA CENTER", lambda w: w.get("dataCenterId")),
    ("VERSION", _worker_version),
    ("UPTIME", lambda w: _uptime(w.get("uptimeSeconds"))),
]


def _workers_view(data: dict):
    from rich.console import Group
    from rich.text import Text

    summary = data["summary"]
    counts = ", ".join(
        f"{summary[status]} {status}"
        for status in ("running", "idle", "initializing", "throttled", "unhealthy")
    )
    version = data.get("endpointVersion")
    suffix = f", endpoint v{version}" if version is not None else ""
    header = Text(f"{summary['total']} workers: {counts}{suffix}", style="dim")
    return (
        Group(header, table(data["workers"], _WORKER_COLUMNS))
        if data["workers"]
        else header
    )


@contextlib.contextmanager
def _data_plane_errors() -> Iterator[None]:
    try:
        yield
    except requests.HTTPError as exc:
        response = exc.response
        if response is None:
            raise click.ClickException(str(exc)) from exc
        detail = response.text.strip() or response.reason or ""
        raise click.ClickException(f"{response.status_code} {detail}".strip()) from exc
    except (requests.RequestException, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc


def _job_path(endpoint_id: str, action: str, job_id: Optional[str] = None) -> str:
    parts = [quote(endpoint_id, safe=""), action]
    if job_id:
        parts.append(quote(job_id, safe=""))
    return "/".join(parts)


def _print_job(job: dict, as_json: bool) -> None:
    if as_json:
        print_json(job)
    elif job["status"] == "COMPLETED":
        output = job.get("output")
        click.echo(output if isinstance(output, str) else json.dumps(output, indent=2))
    if job["status"] != "COMPLETED":
        detail = f": {job['error']}" if job.get("error") else ""
        raise click.ClickException(f"job {job['id']} {job['status'].lower()}{detail}")


def _follow_job(client, endpoint_id, job_id, as_json, *, stream=False, timeout=None):
    """wait for completion, draining streamed chunks before reading the final result."""
    action = "stream" if stream else "status"
    resume = "stream" if stream else "wait"
    deadline = time.monotonic() + timeout if timeout else None
    streamed = open_line = False
    progress = (
        contextlib.nullcontext()
        if stream
        else ui.console.status(f"[dim]waiting for job {job_id} ...[/dim]")
    )
    try:
        with progress:
            while True:
                try:
                    if stream:
                        job = client.get(
                            _job_path(endpoint_id, action, job_id), timeout=60
                        )
                    else:
                        job = client.get(_job_path(endpoint_id, action, job_id))
                except requests.ReadTimeout:
                    if stream:
                        continue
                    raise
                chunks = job["stream"] if stream else []
                streamed = streamed or bool(chunks)
                for chunk in chunks:
                    output = chunk["output"]
                    if isinstance(output, str) and not as_json:
                        click.echo(output, nl=False)
                        open_line = not output.endswith("\n")
                    else:
                        if open_line:
                            click.echo()
                            open_line = False
                        click.echo(json.dumps(output))
                if is_completed(job["status"]) and not chunks:
                    break
                if deadline and time.monotonic() >= deadline:
                    raise click.ClickException(
                        f"job {job_id} is still {job['status']} after {timeout:g}s. "
                        f"resume with: rp endpoint job {resume} {endpoint_id} {job_id}"
                    )
                if not chunks:
                    time.sleep(0.5 if stream else 1)
    except KeyboardInterrupt:
        click.echo(
            f"\njob {job_id} keeps running. resume with: "
            f"rp endpoint job {resume} {endpoint_id} {job_id}",
            err=True,
        )
        raise click.Abort() from None
    if open_line:
        click.echo()
    if streamed and job["status"] == "COMPLETED":
        return
    if stream:
        job = client.get(_job_path(endpoint_id, "status", job_id))
    _print_job(job, as_json)


def _request_body(input_file, data: Optional[str]) -> Optional[Any]:
    if input_file and data:
        raise click.UsageError("use either --input or --data")
    text = input_file.read() if input_file else data
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise click.UsageError(f"request body is not valid JSON: {exc}") from exc


def _call_load_balancer(
    endpoint: dict, path: str, method, body, timeout, as_json
) -> None:
    base = (endpoint.get("requestUrls") or {}).get("base") or (
        f"https://{endpoint['id']}.api.runpod.ai"
    )
    method = (method or ("POST" if body is not None else "GET")).upper()
    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    response = requests.request(
        method,
        url,
        headers={
            "Authorization": f"Bearer {api_key()}",
            "Accept": "application/json",
        },
        json=body,
        timeout=timeout or 600,
    )
    if response.status_code >= 400:
        raise click.ClickException(
            f"{method} {url} returned {response.status_code}: {response.text.strip()}"
        )
    try:
        payload = response.json()
    except ValueError:
        click.echo(response.text)
        return
    if isinstance(payload, str) and not as_json:
        click.echo(payload)
        return
    print_json(payload)


@click.group()
def endpoint():
    """Create, inspect, scale, observe, and call serverless endpoints.

    \b
      rp endpoint list --type queue
      rp endpoint create --name llama --type queue --image IMAGE --gpu h100
      rp endpoint edit ENDPOINT_ID
      rp endpoint logs ENDPOINT_ID --follow
      rp endpoint run ENDPOINT_ID --data '{"prompt":"hello"}'
    """


@endpoint.command(name="list")
@click.option(
    "--name", help="Only endpoints with this name (globs like 'llama*' work)."
)
@click.option(
    "--type",
    "endpoint_type",
    type=click.Choice(list(TYPES), case_sensitive=False),
    help="Only endpoints of this type.",
)
@json_option
def list_endpoints(name, endpoint_type, as_json):
    """List endpoints with their IDs."""
    with api_errors():
        endpoints = list_all("/v2/serverless", "endpoints")
    if name:
        endpoints = [
            e
            for e in endpoints
            if fnmatch.fnmatch((e.get("name") or "").lower(), name.lower())
        ]
    if endpoint_type:
        endpoints = [
            e for e in endpoints if e.get("type") == TYPES[endpoint_type.lower()]
        ]
    if as_json:
        print_json(endpoints)
        return
    print_table(endpoints, _LIST_COLUMNS, "no endpoints match")


@endpoint.command(name="get")
@endpoint_argument(required=False)
@json_option
def get_endpoint(endpoint_id, as_json):
    """Show an endpoint's configuration (picker when ENDPOINT_ID is omitted)."""
    endpoint_id = _pick_endpoint(endpoint_id)
    with api_errors():
        record = run_rest_request("GET", _path(endpoint_id))
    if as_json:
        print_json(record)
        return
    print_fields(record, _FIELDS)


@endpoint.command(name="create")
@_config_options(creating=True)
@json_option
def create_endpoint(as_json, **options):
    """Create an endpoint.

    GPU types are human-readable and resolve against the serverless
    catalog, and regions expand to their data centers.

    \b
      rp endpoint create --name llama-prod --type queue \\
        --image ghcr.io/acme/llama:latest --gpu h100 --max-workers 4 \\
        --region eu --env MODEL_ID=meta-llama/Llama-3.1-8B --flashboot standard
      rp endpoint create --name api --type load-balancer --image IMAGE \\
        --gpu 4090 --requests-per-worker 4 --env PORT=8000
      rp endpoint create --name embeddings --type queue --image IMAGE \\
        --cpu cpu3c --vcpu-count 8
    """
    if not options.get("image") and not options.get("template"):
        raise click.UsageError("pass --image or --template")
    with api_errors():
        body = config.config_body(options)
        created = run_rest_request("POST", "/v2/serverless", json=body)
    if as_json:
        print_json(created)
        return
    ui.success(
        f"created [white]{escape(created['name'])}[/white] [dim]{created['id']}[/dim]"
    )
    print_fields(created, _FIELDS)


@endpoint.command(name="update")
@endpoint_argument()
@_config_options(creating=False)
@click.option(
    "--wait", is_flag=True, help="Follow the rollout until the new release is live."
)
@click.option(
    "--timeout", default=600.0, show_default=True, help="Seconds to follow the rollout."
)
@json_option
def update_endpoint(endpoint_id, wait, timeout, as_json, **options):
    """Change an endpoint's configuration with flags.

    Only the given fields change. --env merges into the existing variables,
    while list flags such as --data-center and --network-volume replace
    their list.
    """
    with api_errors():
        current = run_rest_request("GET", _path(endpoint_id))
        patch = config.config_body(options, current)
    if not patch:
        raise click.UsageError(
            "nothing to update. pass an option (see --help) or use rp endpoint edit"
        )
    _apply(endpoint_id, patch, wait, timeout, as_json)


@endpoint.command(name="edit")
@endpoint_argument(required=False)
@click.option(
    "--file",
    "document",
    type=click.File("r"),
    help="Apply a JSON document (- for stdin) instead of opening $EDITOR.",
)
@click.option("--yes", "-y", is_flag=True, help="Apply without confirming.")
@click.option("--no-follow", is_flag=True, help="Return without following the rollout.")
@click.option(
    "--timeout", default=600.0, show_default=True, help="Seconds to follow the rollout."
)
@json_option
def edit_endpoint(endpoint_id, document, yes, no_follow, timeout, as_json):
    """Edit an endpoint's configuration in $EDITOR.

    Opens the mutable configuration as JSON, validates it, shows the
    patch, and after confirmation applies it and follows the rollout.
    Ctrl-C while following detaches without undoing anything. Fields
    removed from the document stay unchanged.
    """
    endpoint_id = _pick_endpoint(endpoint_id)
    with api_errors():
        current = run_rest_request("GET", _path(endpoint_id))
    before = config.mutable(current)
    if document:
        after = config.validate(document.read(), before)
    elif not interactive():
        raise click.UsageError(
            "edit opens $EDITOR and needs an interactive terminal. "
            "pass --file or use rp endpoint update"
        )
    else:
        after = _edit_document(before)
    patch = config.patch(before, after) if after is not None else {}
    if not patch:
        if as_json:
            print_json({})
        else:
            ui.console.print("[dim]no changes[/dim]")
        return
    if not as_json:
        ui.console.print("[dim]patch:[/dim]")
        print_json(patch)
    confirm(f"apply to endpoint {endpoint_id}?", yes)
    _apply(endpoint_id, patch, not (no_follow or as_json), timeout, as_json)


@endpoint.command(name="scale")
@endpoint_argument()
@_scale_options
@json_option
def scale_endpoint(endpoint_id, as_json, **options):
    """Change worker limits and the autoscaler."""
    with api_errors():
        current = run_rest_request("GET", _path(endpoint_id))
    patch = config.scale_patch(options, current)
    if not patch:
        raise click.UsageError("pass --min-workers, --max-workers, or a scaler option")
    with api_errors():
        updated = run_rest_request("PATCH", _path(endpoint_id), json=patch)
    if as_json:
        print_json(updated)
        return
    ui.success(f"scaled [white]{escape(updated['name'])}[/white]")
    print_fields(
        updated,
        [
            ("id", lambda e: e.get("id") or endpoint_id),
            ("workers", _workers),
            (
                "idle timeout",
                lambda e: _seconds((e.get("workers") or {}).get("idleTimeout")),
            ),
            ("scaling", _scaling),
        ],
    )


@endpoint.command(name="delete")
@endpoint_argument()
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
def delete_endpoint(endpoint_id, yes):
    """Delete an endpoint and its workers."""
    with api_errors():
        name = run_rest_request("GET", _path(endpoint_id))["name"]
    confirm(f"delete endpoint '{name}' ({endpoint_id})?", yes)
    with api_errors():
        run_rest_request("DELETE", _path(endpoint_id))
    ui.success(f"deleted [white]{escape(name)}[/white] [dim]{endpoint_id}[/dim]")


@endpoint.command(name="workers")
@endpoint_argument()
@click.option("--watch", "-w", is_flag=True, help="Refresh in place until Ctrl-C.")
@click.option(
    "--interval",
    type=click.FloatRange(min=1),
    default=2.0,
    show_default=True,
    help="Seconds between refreshes.",
)
@json_option
def list_workers(endpoint_id, watch, interval, as_json):
    """Show an endpoint's workers. With --watch, --json prints one line per refresh."""
    try:
        with api_errors():
            data = run_rest_request("GET", _path(endpoint_id, "workers"))
        if not watch:
            if as_json:
                print_json(data)
            else:
                ui.console.print(_workers_view(data))
            return
        if as_json:
            while True:
                click.echo(json.dumps(data, default=str))
                time.sleep(interval)
                with api_errors():
                    data = run_rest_request("GET", _path(endpoint_id, "workers"))
        from rich.live import Live

        with Live(_workers_view(data), console=ui.console, auto_refresh=False) as live:
            while True:
                time.sleep(interval)
                with api_errors():
                    data = run_rest_request("GET", _path(endpoint_id, "workers"))
                live.update(_workers_view(data), refresh=True)
    except KeyboardInterrupt:
        pass


def _release_changes(release: dict) -> list[str]:
    return [
        entry["field"].removeprefix("template.")
        for entry in release.get("diff") or []
        if entry.get("field") and (entry.get("old") or entry.get("new"))
    ]


@endpoint.command(name="releases")
@endpoint_argument()
@json_option
def list_releases(endpoint_id, as_json):
    """Show release history and rollout progress."""
    with api_errors():
        state = run_rest_request(
            "GET", _path(endpoint_id, "releases"), params={"limit": 1}
        )
        releases = list_all(_path(endpoint_id, "releases"), "releases")
    if as_json:
        print_json(
            {
                "endpointVersion": state.get("endpointVersion"),
                "rollout": state.get("rollout"),
                "releases": releases,
            }
        )
        return
    if state.get("endpointVersion") is not None:
        ui.console.print(f"[dim]{_rollout_line(state)}[/dim]")
    print_table(
        releases,
        [
            ("RELEASE", lambda r: r["id"]),
            (
                "VERSION",
                lambda r: f"v{r['version']}" if r.get("version") is not None else None,
            ),
            ("SOURCE", lambda r: (r.get("source") or "").lower().replace("_", " ")),
            ("BUILD", lambda r: r.get("buildId")),
            ("WORKERS", lambda r: r.get("workerCount")),
            ("CREATED", lambda r: (r.get("createdAt") or "")[:16].replace("T", " ")),
            ("CHANGES", _release_changes),
        ],
        "no releases",
    )


@endpoint.command(name="logs")
@endpoint_argument()
@click.option(
    "--worker",
    "worker_ids",
    multiple=True,
    shell_complete=complete_worker,
    help="Only this worker. Repeatable. Omit for every worker.",
)
@click.option(
    "--follow", "-f", is_flag=True, help="Keep streaming and attach to new workers."
)
@click.option(
    "--source",
    type=click.Choice(["container", "system"]),
    help="Log source. Omit for both.",
)
@click.option(
    "--tail",
    type=click.IntRange(0, 5000),
    default=100,
    show_default=True,
    help="Lines of backfill per worker.",
)
@click.option("--since", help="RFC3339 timestamp to start from (overrides --tail).")
@json_option
def logs(endpoint_id, worker_ids, follow, source, tail, since, as_json):
    """Show worker logs, each line prefixed with its worker id.

    Without --worker, every worker's logs are multiplexed, and with
    --follow newly created workers are attached automatically.
    """
    from runpod.rp_cli.logs import show_logs

    show_logs(
        endpoint_id=endpoint_id,
        worker_ids=worker_ids,
        follow=follow,
        source=source,
        tail=tail,
        since=since,
        as_json=as_json,
    )


@endpoint.command(name="run")
@endpoint_argument()
@click.option(
    "--input",
    "input_file",
    type=click.File("r"),
    help="JSON request file, or - for stdin. A body without an 'input' key is wrapped in one.",
)
@click.option("--data", help="Inline JSON request.")
@click.option("--async", "run_async", is_flag=True, help="Submit and print the job id.")
@click.option("--stream", is_flag=True, help="Print streamed output as it arrives.")
@click.option("--webhook", help="URL notified when the job finishes.")
@click.option(
    "--execution-timeout",
    type=click.IntRange(min=1),
    help="Job execution limit in seconds.",
)
@click.option("--ttl", type=click.IntRange(min=1), help="Job time to live in seconds.")
@click.option(
    "--low-priority", is_flag=True, help="Do not scale up workers for this job."
)
@click.option("--timeout", type=float, help="Seconds to wait for the result.")
@click.option("--path", "lb_path", help="Load-balancer endpoints: request path.")
@click.option(
    "--method",
    help="Load-balancer endpoints: HTTP method (POST with a body, else GET).",
)
@json_option
@_data_plane_errors()
def run_endpoint(
    endpoint_id,
    input_file,
    data,
    run_async,
    stream,
    webhook,
    execution_timeout,
    ttl,
    low_priority,
    timeout,
    lb_path,
    method,
    as_json,
):  # pylint: disable=too-many-arguments
    """Send a request to an endpoint.

    Queue endpoints run a job on the serverless data plane and print its
    output. Load-balancer endpoints receive the body as an HTTP request
    to --path on the endpoint URL.

    \b
      rp endpoint run ENDPOINT_ID --input request.json
      rp endpoint run ENDPOINT_ID --data '{"prompt":"hello"}'
      rp endpoint run ENDPOINT_ID --input - --stream
      rp endpoint run ENDPOINT_ID --input request.json --async
      rp endpoint run LB_ENDPOINT_ID --path /v1/models
    """
    if run_async and stream:
        raise click.UsageError("use either --async or --stream")
    body = _request_body(input_file, data)
    with api_errors():
        record = run_rest_request("GET", _path(endpoint_id))

    if record.get("type") == "LOAD_BALANCER":
        if run_async or stream or webhook or execution_timeout or ttl or low_priority:
            raise click.UsageError(
                "load-balancer endpoints take --path, --method, and a body; "
                "job options apply to queue endpoints"
            )
        _call_load_balancer(record, lb_path or "/", method, body, timeout, as_json)
        return

    if lb_path or method:
        raise click.UsageError("--path and --method apply to load-balancer endpoints")
    if body is None:
        raise click.UsageError("pass --input FILE or --data JSON")
    request = body if isinstance(body, dict) and "input" in body else {"input": body}
    if webhook:
        request["webhook"] = webhook
    policy = dict(request.get("policy") or {})
    if execution_timeout:
        policy["executionTimeout"] = execution_timeout * 1000
    if ttl:
        policy["ttl"] = ttl * 1000
    if low_priority:
        policy["lowPriority"] = True
    if policy:
        request["policy"] = policy

    client = runner.RunPodClient()
    submitted = client.post(_job_path(endpoint_id, "run"), request)
    job_id = submitted["id"]
    if run_async:
        if as_json:
            print_json(submitted)
            return
        ui.success(
            f"submitted job [white]{job_id}[/white] [dim]{submitted.get('status')}[/dim]"
        )
        ui.console.print(f"[dim]rp endpoint job wait {endpoint_id} {job_id}[/dim]")
        return
    if stream:
        if not as_json:
            click.echo(f"job {job_id}", err=True)
        _follow_job(client, endpoint_id, job_id, as_json, stream=True)
        return
    _follow_job(client, endpoint_id, job_id, as_json, timeout=timeout)


@endpoint.group(name="job")
def job():
    """Inspect and control queue jobs."""


def _job_command(name: str):
    def decorate(func):
        func = json_option(func)
        func = click.argument("job_id")(func)
        func = endpoint_argument()(func)
        return job.command(name=name)(func)

    return decorate


def _job_output(record: dict) -> Optional[str]:
    output = record.get("output")
    if output is None or isinstance(output, str):
        return output
    return json.dumps(output)


_JOB_FIELDS = [
    ("id", lambda j: j.get("id")),
    ("status", lambda j: (j.get("status") or "").lower()),
    ("delay", lambda j: _seconds((j.get("delayTime") or 0) / 1000)),
    ("execution", lambda j: _seconds((j.get("executionTime") or 0) / 1000)),
    ("worker", lambda j: j.get("workerId")),
    ("error", lambda j: j.get("error")),
    ("output", _job_output),
]


@_job_command("get")
@_data_plane_errors()
def job_get(endpoint_id, job_id, as_json):
    """Show a job's status and output."""
    record = runner.RunPodClient().get(_job_path(endpoint_id, "status", job_id))
    if as_json:
        print_json(record)
        return
    print_fields(record, _JOB_FIELDS)


@_job_command("wait")
@click.option("--timeout", type=float, help="Seconds to wait.")
@_data_plane_errors()
def job_wait(endpoint_id, job_id, timeout, as_json):
    """Wait for a job to finish and print its output."""
    _follow_job(runner.RunPodClient(), endpoint_id, job_id, as_json, timeout=timeout)


@_job_command("stream")
@_data_plane_errors()
def job_stream(endpoint_id, job_id, as_json):
    """Print a job's streamed output as it arrives."""
    _follow_job(runner.RunPodClient(), endpoint_id, job_id, as_json, stream=True)


@_job_command("cancel")
@_data_plane_errors()
def job_cancel(endpoint_id, job_id, as_json):
    """Cancel a queued or running job."""
    record = runner.RunPodClient().post(_job_path(endpoint_id, "cancel", job_id), None)
    if as_json:
        print_json(record)
        return
    ui.success(f"job [white]{job_id}[/white] [dim]{record.get('status')}[/dim]")


@_job_command("retry")
@_data_plane_errors()
def job_retry(endpoint_id, job_id, as_json):
    """Requeue a failed or timed-out job."""
    record = runner.RunPodClient().post(_job_path(endpoint_id, "retry", job_id), None)
    if as_json:
        print_json(record)
        return
    ui.success(
        f"job [white]{record.get('id') or job_id}[/white] [dim]{record.get('status')}[/dim]"
    )


@endpoint.command(name="purge")
@endpoint_argument()
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
@json_option
@_data_plane_errors()
def purge_queue(endpoint_id, yes, as_json):
    """Remove every queued job. Running jobs are not affected."""
    confirm(f"remove every queued job from {endpoint_id}?", yes)
    record = runner.RunPodClient().post(_job_path(endpoint_id, "purge-queue"), None)
    if as_json:
        print_json(record)
        return
    ui.success(
        f"purged {record.get('removed', 0)} queued jobs from [white]{endpoint_id}[/white]"
    )
