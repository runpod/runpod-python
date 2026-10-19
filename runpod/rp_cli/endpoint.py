"""`rp endpoint`: create, inspect, scale, observe, and call serverless endpoints.

configuration goes through the rest v2 control plane (/v2/serverless).
jobs go through the serverless data plane (runpod.endpoint_url_base),
which has no rest v2 equivalent.
"""

import contextlib
import fnmatch
import json
import queue
import shlex
import threading
import time
from datetime import datetime, timezone
from typing import Any, Iterator, Optional
from urllib.parse import quote

import click
import requests

from runpod import error
from runpod.api.rest import (
    _build_headers,
    _build_url,
    _raise_for_error,
    _resolve_api_key,
    run_rest_request,
)
from runpod.endpoint.helpers import is_completed
from runpod.rp_cli import compute
from runpod.rp_cli.output import (
    api_errors,
    cell,
    completer,
    confirm,
    interactive,
    json_option,
    pick,
    print_fields,
    print_json,
)

TYPES = {"queue": "QUEUE", "load-balancer": "LOAD_BALANCER"}
FLASHBOOT = {"off": "OFF", "standard": "FLASHBOOT", "priority": "PRIORITY_FLASHBOOT"}
DEFAULT_QUEUE_DELAY = 4
WORKER_POLL_SECONDS = 5.0
STREAM_POLL_SECONDS = 60

MUTABLE_FIELDS = {
    "name": (str,),
    "image": (str,),
    "args": (str,),
    "env": (dict,),
    "disk": (int,),
    "ports": (list,),
    "registry": (str, type(None)),
    "gpu": (dict,),
    "cpu": (list,),
    "workers": (dict,),
    "scaling": (dict,),
    "timeout": (int,),
    "flashboot": (str,),
    "dataCenterIds": (list,),
    "mounts": (dict,),
}
NESTED_FIELDS = {
    "gpu": {"pools", "excludedTypes", "count", "allowedCudaVersions", "minCudaVersion"},
    "workers": {"min", "max", "idleTimeout"},
    "scaling": {"type", "queueDelay", "requestCount"},
    "mounts": {"network", "global"},
}


def _path(endpoint_id: str, *parts: str) -> str:
    segments = [quote(value, safe="") for value in (endpoint_id, *parts)]
    return "/v2/serverless/" + "/".join(segments)


def _get(endpoint_id: str) -> dict:
    return run_rest_request("GET", _path(endpoint_id)) or {}


def _list_endpoints() -> list[dict]:
    return compute.list_all("/v2/serverless", "endpoints")


def _list_workers(endpoint_id: str) -> dict:
    return run_rest_request("GET", _path(endpoint_id, "workers")) or {}


def _choices(path: str, collection: str):
    def fetch():
        return [
            (record["id"], record.get("name") or "")
            for record in compute.list_all(path, collection)
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
    """worker ids of the endpoint named earlier on the command line."""
    endpoint_id = ctx.params.get("endpoint_id")
    if not endpoint_id:
        return []
    return completer(
        lambda: [
            (worker["id"], (worker.get("status") or "").lower())
            for worker in _list_workers(endpoint_id).get("workers") or []
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
        endpoints = _list_endpoints()
    return pick(
        "endpoint",
        [
            (e["id"], f"{e['id']}  {e.get('name') or ''}  {_type(e) or ''}")
            for e in endpoints
        ],
        "pass an ENDPOINT_ID",
    )


# ----------------------------------------------------------------- display


def _type(endpoint: dict) -> Optional[str]:
    names = {value: key for key, value in TYPES.items()}
    return names.get(endpoint.get("type"), endpoint.get("type"))


def _flashboot(endpoint: dict) -> Optional[str]:
    names = {value: key for key, value in FLASHBOOT.items()}
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


def _escape(text: Any) -> str:
    from rich.markup import escape

    return escape(str(text))


def _table(rows: list[dict], columns: list):
    """an aligned table whose first column, the id, never wraps or truncates."""
    from rich.markup import escape
    from rich.table import Table

    table = Table(box=None, header_style="dim", pad_edge=False)
    for index, (header, _) in enumerate(columns):
        table.add_column(header, no_wrap=index == 0)
    for row in rows:
        table.add_row(*(escape(cell(value(row))) for _, value in columns))
    return table


def _print_table(rows: list[dict], columns: list, empty: str) -> None:
    from runpod.rp_cli import console as ui

    ui.console.print(_table(rows, columns) if rows else f"[dim]{empty}[/dim]")


# ------------------------------------------------------------ request body


def _env_pairs(values: tuple[str, ...]) -> dict[str, str]:
    env = {}
    for value in values:
        key, sep, val = value.partition("=")
        if not sep or not key:
            raise click.UsageError(f"invalid --env '{value}'. use KEY=VALUE")
        env[key] = val
    return env


def _gpu_body(specs, count, cuda, updating: bool) -> dict:
    gpu: dict[str, Any] = {}
    if specs:
        catalog = [
            entry
            for entry in compute.list_gpus(product="SERVERLESS")
            if entry.get("pool")
        ]
        matches: list[dict] = []
        counts = {count} if count else set()
        for spec in specs:
            name, spec_count = compute.parse_gpu_spec(spec)
            if spec_count:
                counts.add(spec_count)
            for match in compute.resolve_gpus(name, catalog):
                if match not in matches:
                    matches.append(match)
        if len(counts) > 1:
            raise click.UsageError("conflicting gpu counts; use one count per worker")
        pools, excluded = compute.endpoint_gpu_selection(matches, catalog)
        gpu["pools"] = pools
        if excluded or updating:
            gpu["excludedTypes"] = excluded
        count = counts.pop() if counts else None
    if count:
        gpu["count"] = count
    if cuda:
        if [value.lower() for value in cuda] == ["any"]:
            gpu["allowedCudaVersions"] = []
            if updating:
                gpu["minCudaVersion"] = ""
            return gpu
        minimum, exact = compute.parse_cuda(cuda)
        if minimum:
            gpu["minCudaVersion"] = minimum
            if updating:
                gpu["allowedCudaVersions"] = []
        else:
            gpu["allowedCudaVersions"] = exact
            if updating:
                gpu["minCudaVersion"] = ""
    return gpu


def _cpu_body(specs, vcpu_count, current: Optional[dict]) -> Optional[list]:
    if not specs:
        if vcpu_count and current and current.get("cpu"):
            return [
                {"id": cpu["id"], "vcpuCount": vcpu_count} for cpu in current["cpu"]
            ]
        if vcpu_count:
            raise click.UsageError("--vcpu-count applies to CPU endpoints (pass --cpu)")
        return None
    flavors = compute.list_cpus(product="SERVERLESS")
    entries = []
    for spec in specs:
        name, sep, count = spec.rpartition(":")
        if not sep:
            name, count = spec, ""
        if count and not count.isdigit():
            raise click.UsageError(
                f"invalid --cpu '{spec}'. use a flavor like cpu3c or cpu3c:8"
            )
        vcpus = int(count) if count else vcpu_count
        if not vcpus:
            raise click.UsageError(f"pass --vcpu-count or --cpu {name}:VCPUS")
        entries.append(
            {"id": compute.resolve_cpu(name, flavors)["id"], "vcpuCount": vcpus}
        )
    return entries


def _workers_body(options: dict, current: Optional[dict]) -> Optional[dict]:
    changes = {
        key: options[name]
        for key, name in (
            ("min", "min_workers"),
            ("max", "max_workers"),
            ("idleTimeout", "idle_timeout_seconds"),
        )
        if options.get(name) is not None
    }
    if not changes:
        return None
    existing = (current or {}).get("workers") or {}
    merged = {
        key: existing[key]
        for key in ("min", "max", "idleTimeout")
        if existing.get(key) is not None
    }
    merged.update(changes)
    return merged


def _scaling_body(options: dict, endpoint_type: Optional[str]) -> Optional[dict]:
    delay = options.get("queue_delay_seconds")
    per_worker = options.get("requests_per_worker")
    if delay is not None and per_worker is not None:
        raise click.UsageError(
            "use either --queue-delay-seconds or --requests-per-worker"
        )
    if delay is not None:
        if endpoint_type == "LOAD_BALANCER":
            raise click.UsageError(
                "load-balancer endpoints scale on --requests-per-worker"
            )
        return {"type": "QUEUE_DELAY", "queueDelay": delay}
    if per_worker is not None:
        return {"type": "REQUEST_COUNT", "requestCount": per_worker}
    return None


def _mounts_body(options: dict, current: Optional[dict]) -> Optional[dict]:
    network = options.get("network_volume") or ()
    global_ = options.get("global_volume") or ()
    if not network and not global_:
        return None
    existing = (current or {}).get("mounts") or {}
    mounts = {}
    for kind, ids in (("network", network), ("global", global_)):
        if ids:
            mounts[kind] = [{"volumeId": volume} for volume in ids]
        elif existing.get(kind):
            mounts[kind] = [{"volumeId": m["volumeId"]} for m in existing[kind]]
    return mounts


def _env_body(options: dict, current: Optional[dict]) -> Optional[dict]:
    env = _env_pairs(options.get("env") or ())
    unset = options.get("unset_env") or ()
    if current is None or not (env or unset):
        return env or None
    merged = dict(current.get("env") or {})
    merged.update(env)
    for key in unset:
        if key not in merged:
            raise click.UsageError(f"--unset-env {key}: no such variable")
        merged.pop(key)
    return merged


def _check_compute(options: dict, current: Optional[dict]) -> None:
    current = current or {}
    if options.get("gpu") and options.get("cpu"):
        raise click.UsageError("use either --gpu or --cpu")
    if options.get("gpu") and current.get("cpu"):
        raise click.UsageError("a CPU endpoint cannot switch to GPUs")
    if options.get("cpu") and current.get("gpu"):
        raise click.UsageError("a GPU endpoint cannot switch to CPUs")
    if options.get("cuda") and (options.get("cpu") or current.get("cpu")):
        raise click.UsageError("--cuda applies to GPU endpoints")


def _config_body(options: dict, current: Optional[dict] = None) -> dict:
    """request body for a create (current is None) or an update."""
    updating = current is not None
    endpoint_type = current.get("type") if updating else options.get("endpoint_type")
    body: dict[str, Any] = {}
    if not updating:
        body["type"] = endpoint_type

    for key, name in (
        ("name", "name"),
        ("image", "image"),
        ("templateId", "template"),
        ("args", "args"),
        ("disk", "disk"),
        ("registry", "registry"),
    ):
        if options.get(name) is not None:
            body[key] = options[name]
    for key in ("entrypoint", "cmd"):
        if options.get(key) is not None:
            body[key] = shlex.split(options[key])
    if options.get("port"):
        body["ports"] = list(options["port"])
    if options.get("flashboot"):
        body["flashboot"] = FLASHBOOT[options["flashboot"].lower()]
    if options.get("request_timeout_seconds") is not None:
        body["timeout"] = options["request_timeout_seconds"] * 1000
    env = _env_body(options, current)
    if env is not None:
        body["env"] = env

    _check_compute(options, current)
    gpu = _gpu_body(
        options.get("gpu"), options.get("gpu_count"), options.get("cuda"), updating
    )
    if gpu:
        body["gpu"] = gpu
    cpu = _cpu_body(options.get("cpu"), options.get("vcpu_count"), current)
    if cpu:
        body["cpu"] = cpu

    workers = _workers_body(options, current)
    if workers:
        body["workers"] = workers
    scaling = _scaling_body(options, endpoint_type)
    if scaling:
        body["scaling"] = scaling
    elif not updating:
        if endpoint_type == "LOAD_BALANCER":
            raise click.UsageError("load-balancer endpoints need --requests-per-worker")
        body["scaling"] = {"type": "QUEUE_DELAY", "queueDelay": DEFAULT_QUEUE_DELAY}

    places = tuple(options.get("data_center") or ()) + tuple(
        options.get("region") or ()
    )
    if places:
        body["dataCenterIds"] = compute.resolve_regions(
            places, compute.list_datacenters()
        )
    mounts = _mounts_body(options, current)
    if mounts:
        body["mounts"] = mounts
    return body


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
    """the flags shared by create and update, one per api field."""
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
            type=click.Choice(list(FLASHBOOT), case_sensitive=False),
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


# ------------------------------------------------------------------ rollout


def _rollout_state(endpoint_id: str) -> dict:
    return (
        run_rest_request("GET", _path(endpoint_id, "releases"), params={"limit": 1})
        or {}
    )


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
    """poll releases until the new version runs on every worker."""
    from runpod.rp_cli import console as ui

    started = time.monotonic()
    last = None
    try:
        while True:
            with api_errors():
                state = _rollout_state(endpoint_id)
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
    from runpod.rp_cli import console as ui

    with api_errors():
        before = _rollout_state(endpoint_id).get("endpointVersion") if follow else None
        updated = run_rest_request("PATCH", _path(endpoint_id), json=patch) or {}
    if as_json:
        print_json(updated)
    else:
        ui.success(
            f"updated [white]{_escape(updated.get('name') or endpoint_id)}[/white] "
            f"[dim]{endpoint_id}[/dim]"
        )
    if follow:
        _follow_rollout(endpoint_id, before, timeout)


# --------------------------------------------------------------------- edit


def _mutable(endpoint: dict) -> dict:
    """the endpoint's configuration as UpdateEndpointRequest fields."""
    doc: dict[str, Any] = {}
    for key in ("name", "image", "args", "env", "disk", "ports"):
        if endpoint.get(key) is not None:
            doc[key] = endpoint[key]
    doc["registry"] = endpoint.get("registry")
    gpu = endpoint.get("gpu")
    if gpu:
        doc["gpu"] = {
            "pools": gpu.get("pools") or [],
            "excludedTypes": gpu.get("excludedTypes") or [],
            "count": gpu.get("count") or 1,
            "allowedCudaVersions": gpu.get("allowedCudaVersions") or [],
            "minCudaVersion": gpu.get("minCudaVersion"),
        }
    if endpoint.get("cpu"):
        doc["cpu"] = [
            {"id": cpu["id"], "vcpuCount": cpu["vcpuCount"]} for cpu in endpoint["cpu"]
        ]
    for key in ("workers", "scaling", "timeout", "flashboot", "dataCenterIds"):
        if endpoint.get(key) is not None:
            doc[key] = endpoint[key]
    mounts = endpoint.get("mounts") or {}
    doc["mounts"] = {
        kind: [{"volumeId": m["volumeId"]} for m in mounts.get(kind) or []]
        for kind in ("network", "global")
    }
    return doc


def _kind(kind: type) -> str:
    return {
        str: "a string",
        int: "an integer",
        dict: "an object",
        list: "a list",
        type(None): "null",
    }[kind]


def _validate(text: str, before: dict) -> dict:
    """parse an edited document, raising ClickException on any problem."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"invalid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise click.ClickException("the document must be a JSON object")
    problems = []
    for key, value in doc.items():
        kinds = MUTABLE_FIELDS.get(key)
        if kinds is None:
            problems.append(f"unknown field '{key}'")
            continue
        if isinstance(value, bool) or not isinstance(value, kinds):
            problems.append(f"'{key}' must be {' or '.join(_kind(k) for k in kinds)}")
            continue
        allowed = NESTED_FIELDS.get(key)
        if allowed:
            problems.extend(
                f"unknown field '{key}.{nested}'"
                for nested in value
                if nested not in allowed
            )
    env = doc.get("env")
    if isinstance(env, dict) and not all(isinstance(v, str) for v in env.values()):
        problems.append("env values must be strings")
    flashboot = doc.get("flashboot")
    if isinstance(flashboot, str) and flashboot not in FLASHBOOT.values():
        problems.append(f"'flashboot' must be one of {', '.join(FLASHBOOT.values())}")
    if "gpu" in doc and "gpu" not in before:
        problems.append("a CPU endpoint cannot switch to GPUs")
    if "cpu" in doc and "cpu" not in before:
        problems.append("a GPU endpoint cannot switch to CPUs")
    if problems:
        raise click.ClickException("; ".join(problems))
    return doc


def _patch(before: dict, after: dict) -> dict:
    """top-level fields that changed, with gpu narrowed to its changed keys."""
    patch = {}
    for key, value in after.items():
        if before.get(key) == value:
            continue
        if key != "gpu":
            patch[key] = value
            continue
        old = before.get("gpu") or {}
        gpu = {k: v for k, v in value.items() if old.get(k) != v}
        if "pools" in gpu or "excludedTypes" in gpu:
            gpu["pools"] = value.get("pools", old.get("pools"))
            gpu["excludedTypes"] = value.get("excludedTypes") or []
        if "minCudaVersion" in gpu and gpu["minCudaVersion"] is None:
            gpu["minCudaVersion"] = ""
        patch["gpu"] = gpu
    return patch


def _edit_document(before: dict) -> Optional[dict]:
    """open the document in $EDITOR until it validates or the user gives up."""
    from runpod.rp_cli import console as ui

    text = json.dumps(before, indent=2) + "\n"
    while True:
        edited = click.edit(text, extension=".json")
        if edited is None:
            return None
        try:
            return _validate(edited, before)
        except click.ClickException as exc:
            ui.error(_escape(exc.message))
            if not click.confirm("re-open the editor?", default=True):
                raise
            text = edited


# ------------------------------------------------------------------ workers


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


def _worker_summary(data: dict) -> str:
    summary = data.get("summary") or {}
    counts = ", ".join(
        f"{summary.get(status, 0)} {status}"
        for status in ("running", "idle", "initializing", "throttled", "unhealthy")
    )
    version = data.get("endpointVersion")
    suffix = f", endpoint v{version}" if version is not None else ""
    return f"{summary.get('total', 0)} workers: {counts}{suffix}"


def _workers_view(data: dict):
    from rich.console import Group
    from rich.text import Text

    header = Text(_worker_summary(data), style="dim")
    workers = data.get("workers") or []
    if not workers:
        return header
    return Group(header, _table(workers, _WORKER_COLUMNS))


# --------------------------------------------------------------------- logs


def _log_stream(
    endpoint_id: str,
    worker_id: str,
    params: dict,
    resume: Optional[str],
    events: "queue.Queue",
    stop: threading.Event,
    read_timeout: float,
) -> None:
    """push one worker's sse log events onto the shared queue."""
    path = _path(endpoint_id, "workers", worker_id, "logs")
    event_id = None
    try:
        headers = dict(
            _build_headers(_resolve_api_key(None)), Accept="text/event-stream"
        )
        if resume:
            headers["Last-Event-ID"] = resume
        with requests.get(
            _build_url(path),
            params=params,
            headers=headers,
            stream=True,
            timeout=(10, read_timeout),
        ) as response:
            if response.status_code >= 400:
                _raise_for_error(response.status_code, "GET", path, response.text)
            for raw in response.iter_lines(decode_unicode=True):
                if stop.is_set():
                    return
                if raw.startswith("id:"):
                    event_id = raw[3:].strip()
                elif raw.startswith("data:"):
                    try:
                        entry = json.loads(raw[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    events.put(("line", worker_id, entry, event_id or entry.get("ts")))
    except requests.exceptions.RequestException:
        pass
    except error.RunPodError as exc:
        events.put(("error", worker_id, exc, None))
    finally:
        events.put(("done", worker_id, None, None))


def _log_line(worker_id: str, entry: dict, width: int, as_json: bool) -> str:
    if as_json:
        return json.dumps({"worker": worker_id, **entry})
    return (
        f"{worker_id:<{width}} [{entry.get('source') or '?'}] {entry.get('line', '')}"
    )


def _is_live(entry: dict, started: datetime) -> bool:
    try:
        stamp = datetime.fromisoformat(str(entry.get("ts")).replace("Z", "+00:00"))
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp >= started


# --------------------------------------------------------------- data plane


@contextlib.contextmanager
def _data_plane_errors() -> Iterator[None]:
    """surface serverless data-plane failures as clean cli errors."""
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


def _client():
    from runpod.endpoint.runner import RunPodClient

    with _data_plane_errors():
        return RunPodClient()


def _job_path(endpoint_id: str, action: str, job_id: Optional[str] = None) -> str:
    parts = [quote(endpoint_id, safe=""), action]
    if job_id:
        parts.append(quote(job_id, safe=""))
    return "/".join(parts)


def _job_failure(job: dict) -> click.ClickException:
    detail = job.get("error")
    status = str(job.get("status") or "unknown").lower()
    return click.ClickException(
        f"job {job.get('id')} {status}" + (f": {detail}" if detail else "")
    )


def _wait_job(client, endpoint_id: str, job_id: str, timeout: Optional[float]) -> dict:
    from runpod.rp_cli import console as ui

    deadline = time.monotonic() + timeout if timeout else None
    try:
        with ui.console.status(f"[dim]waiting for job {job_id} ...[/dim]"):
            while True:
                with _data_plane_errors():
                    job = client.get(_job_path(endpoint_id, "status", job_id))
                if is_completed(job.get("status")):
                    return job
                if deadline and time.monotonic() >= deadline:
                    raise click.ClickException(
                        f"job {job_id} is still {job.get('status')} after {timeout:g}s. "
                        f"resume with: rp endpoint job wait {endpoint_id} {job_id}"
                    )
                time.sleep(1)
    except KeyboardInterrupt:
        click.echo(
            f"\njob {job_id} keeps running. resume with: "
            f"rp endpoint job wait {endpoint_id} {job_id}",
            err=True,
        )
        raise click.Abort() from None


def _print_job(job: dict, as_json: bool) -> None:
    if as_json:
        print_json(job)
    elif job.get("status") == "COMPLETED":
        output = job.get("output")
        click.echo(output if isinstance(output, str) else json.dumps(output, indent=2))
    if job.get("status") != "COMPLETED":
        raise _job_failure(job)


def _stream_job(client, endpoint_id: str, job_id: str, as_json: bool) -> None:
    """print streamed chunks, or the final output when the handler streams nothing."""
    open_line = False
    streamed = False
    try:
        while True:
            with _data_plane_errors():
                try:
                    partial = client.get(
                        _job_path(endpoint_id, "stream", job_id),
                        timeout=STREAM_POLL_SECONDS,
                    )
                except requests.exceptions.ReadTimeout:
                    continue
            chunks = partial.get("stream") or []
            streamed = streamed or bool(chunks)
            for chunk in chunks:
                output = chunk.get("output")
                if as_json:
                    click.echo(json.dumps(output))
                elif isinstance(output, str):
                    click.echo(output, nl=False)
                    open_line = not output.endswith("\n")
                else:
                    if open_line:
                        click.echo()
                        open_line = False
                    click.echo(json.dumps(output))
            if is_completed(partial.get("status")) and not chunks:
                break
            if not chunks:
                time.sleep(0.5)
    except KeyboardInterrupt:
        click.echo(
            f"\njob {job_id} keeps running. resume with: "
            f"rp endpoint job stream {endpoint_id} {job_id}",
            err=True,
        )
        raise click.Abort() from None
    if open_line:
        click.echo()
    if streamed and partial.get("status") == "COMPLETED":
        return
    with _data_plane_errors():
        final = client.get(_job_path(endpoint_id, "status", job_id))
    _print_job(final, as_json)


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
    with _data_plane_errors():
        response = requests.request(
            method,
            url,
            headers=_build_headers(_resolve_api_key(None)),
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


# ----------------------------------------------------------------- commands


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
        endpoints = _list_endpoints()
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
    _print_table(endpoints, _LIST_COLUMNS, "no endpoints match")


@endpoint.command(name="get")
@endpoint_argument(required=False)
@json_option
def get_endpoint(endpoint_id, as_json):
    """Show an endpoint's configuration (picker when ENDPOINT_ID is omitted)."""
    endpoint_id = _pick_endpoint(endpoint_id)
    with api_errors():
        record = _get(endpoint_id)
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
    from runpod.rp_cli import console as ui

    if not options.get("image") and not options.get("template"):
        raise click.UsageError("pass --image or --template")
    with api_errors():
        body = _config_body(options)
        created = run_rest_request("POST", "/v2/serverless", json=body) or {}
    if as_json:
        print_json(created)
        return
    ui.success(
        f"created [white]{_escape(created.get('name'))}[/white] [dim]{created.get('id')}[/dim]"
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
        current = _get(endpoint_id)
        patch = _config_body(options, current)
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
    from runpod.rp_cli import console as ui

    endpoint_id = _pick_endpoint(endpoint_id)
    with api_errors():
        current = _get(endpoint_id)
    before = _mutable(current)
    if document:
        after = _validate(document.read(), before)
    elif not interactive():
        raise click.UsageError(
            "edit opens $EDITOR and needs an interactive terminal. "
            "pass --file or use rp endpoint update"
        )
    else:
        after = _edit_document(before)
    patch = _patch(before, after) if after is not None else {}
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
    from runpod.rp_cli import console as ui

    with api_errors():
        current = _get(endpoint_id)
    patch = {}
    workers = _workers_body(options, current)
    if workers:
        patch["workers"] = workers
    scaling = _scaling_body(options, current.get("type"))
    if scaling:
        patch["scaling"] = scaling
    if not patch:
        raise click.UsageError("pass --min-workers, --max-workers, or a scaler option")
    with api_errors():
        updated = run_rest_request("PATCH", _path(endpoint_id), json=patch) or {}
    if as_json:
        print_json(updated)
        return
    ui.success(f"scaled [white]{_escape(updated.get('name') or endpoint_id)}[/white]")
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
    from runpod.rp_cli import console as ui

    with api_errors():
        name = _get(endpoint_id).get("name") or endpoint_id
    confirm(f"delete endpoint '{name}' ({endpoint_id})?", yes)
    with api_errors():
        run_rest_request("DELETE", _path(endpoint_id))
    ui.success(f"deleted [white]{_escape(name)}[/white] [dim]{endpoint_id}[/dim]")


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
    from runpod.rp_cli import console as ui

    try:
        with api_errors():
            data = _list_workers(endpoint_id)
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
                    data = _list_workers(endpoint_id)
        from rich.live import Live

        with Live(_workers_view(data), console=ui.console, auto_refresh=False) as live:
            while True:
                time.sleep(interval)
                with api_errors():
                    data = _list_workers(endpoint_id)
                live.update(_workers_view(data), refresh=True)
    except KeyboardInterrupt:
        pass


def _release_changes(release: dict) -> list[str]:
    """changed field names, skipping entries whose old and new values are both empty."""
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
    from runpod.rp_cli import console as ui

    with api_errors():
        state = _rollout_state(endpoint_id)
        releases = compute.list_all(_path(endpoint_id, "releases"), "releases")
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
    _print_table(
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
    from runpod.rp_cli import console as ui

    params: dict[str, Any] = {"tail": tail}
    if source:
        params["source"] = source
    if since:
        params["since"] = since
    wanted = set(worker_ids)
    started = datetime.now(timezone.utc)
    events: queue.Queue = queue.Queue()
    stop = threading.Event()
    attached: set[str] = set()
    cursors: dict[str, str] = {}
    collected: list[tuple[str, dict]] = []
    width = max((len(w) for w in wanted), default=0)

    def attach_new() -> None:
        nonlocal width
        with api_errors():
            listed = [w["id"] for w in _list_workers(endpoint_id).get("workers") or []]
        for worker_id in listed:
            if (wanted and worker_id not in wanted) or worker_id in attached:
                continue
            attached.add(worker_id)
            width = max(width, len(worker_id))
            threading.Thread(
                target=_log_stream,
                args=(
                    endpoint_id,
                    worker_id,
                    params,
                    cursors.get(worker_id),
                    events,
                    stop,
                    90.0 if follow else 3.0,
                ),
                daemon=True,
            ).start()

    try:
        attach_new()
        if not attached:
            if not follow:
                ui.console.print("[dim]no workers[/dim]")
                return
            ui.console.print("[dim]waiting for workers ...[/dim]")
        next_poll = time.monotonic() + WORKER_POLL_SECONDS
        while follow or attached:
            try:
                kind, worker_id, payload, cursor = events.get(timeout=0.5)
            except queue.Empty:
                kind = None
            if kind == "line":
                if cursor:
                    cursors[worker_id] = cursor
                if follow:
                    click.echo(_log_line(worker_id, payload, width, as_json))
                elif _is_live(payload, started):
                    attached.discard(worker_id)
                else:
                    collected.append((worker_id, payload))
            elif kind == "error":
                if not (
                    isinstance(payload, error.QueryError) and payload.status_code == 404
                ):
                    raise click.ClickException(str(payload))
            elif kind == "done":
                attached.discard(worker_id)
            if follow and time.monotonic() >= next_poll:
                attach_new()
                next_poll = time.monotonic() + WORKER_POLL_SECONDS
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
    for worker_id, entry in sorted(
        collected, key=lambda item: str(item[1].get("ts") or "")
    ):
        click.echo(_log_line(worker_id, entry, width, as_json))


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
    from runpod.rp_cli import console as ui

    if run_async and stream:
        raise click.UsageError("use either --async or --stream")
    body = _request_body(input_file, data)
    with api_errors():
        record = _get(endpoint_id)

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

    client = _client()
    with _data_plane_errors():
        submitted = client.post(_job_path(endpoint_id, "run"), request)
    job_id = submitted.get("id")
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
        _stream_job(client, endpoint_id, job_id, as_json)
        return
    _print_job(_wait_job(client, endpoint_id, job_id, timeout), as_json)


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
def job_get(endpoint_id, job_id, as_json):
    """Show a job's status and output."""
    with _data_plane_errors():
        record = _client().get(_job_path(endpoint_id, "status", job_id))
    if as_json:
        print_json(record)
        return
    print_fields(record, _JOB_FIELDS)


@_job_command("wait")
@click.option("--timeout", type=float, help="Seconds to wait.")
def job_wait(endpoint_id, job_id, timeout, as_json):
    """Wait for a job to finish and print its output."""
    _print_job(_wait_job(_client(), endpoint_id, job_id, timeout), as_json)


@_job_command("stream")
def job_stream(endpoint_id, job_id, as_json):
    """Print a job's streamed output as it arrives."""
    _stream_job(_client(), endpoint_id, job_id, as_json)


@_job_command("cancel")
def job_cancel(endpoint_id, job_id, as_json):
    """Cancel a queued or running job."""
    from runpod.rp_cli import console as ui

    with _data_plane_errors():
        record = _client().post(_job_path(endpoint_id, "cancel", job_id), None)
    if as_json:
        print_json(record)
        return
    ui.success(f"job [white]{job_id}[/white] [dim]{record.get('status')}[/dim]")


@_job_command("retry")
def job_retry(endpoint_id, job_id, as_json):
    """Requeue a failed or timed-out job."""
    from runpod.rp_cli import console as ui

    with _data_plane_errors():
        record = _client().post(_job_path(endpoint_id, "retry", job_id), None)
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
def purge_queue(endpoint_id, yes, as_json):
    """Remove every queued job. Running jobs are not affected."""
    from runpod.rp_cli import console as ui

    confirm(f"remove every queued job from {endpoint_id}?", yes)
    with _data_plane_errors():
        record = _client().post(_job_path(endpoint_id, "purge-queue"), None)
    if as_json:
        print_json(record)
        return
    ui.success(
        f"purged {record.get('removed', 0)} queued jobs from [white]{endpoint_id}[/white]"
    )
