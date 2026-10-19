"""endpoint request configuration and editable JSON documents."""

import json
import shlex
from typing import Any, Optional

import click

from runpod.rp_cli import compute
from runpod.rp_cli.options import parse_env

FLASHBOOT = {"off": "OFF", "standard": "FLASHBOOT", "priority": "PRIORITY_FLASHBOOT"}


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


def _gpu(specs, count, cuda, updating: bool) -> dict:
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


def _cpu(specs, vcpu_count, current: Optional[dict]) -> Optional[list]:
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


def scale_patch(options: dict, current: dict) -> dict:
    patch = {}
    changes = {
        key: options[flag]
        for key, flag in (
            ("min", "min_workers"),
            ("max", "max_workers"),
            ("idleTimeout", "idle_timeout_seconds"),
        )
        if options.get(flag) is not None
    }
    if changes:
        patch["workers"] = {**current.get("workers", {}), **changes}
    delay = options.get("queue_delay_seconds")
    count = options.get("requests_per_worker")
    if delay is not None and count is not None:
        raise click.UsageError(
            "use either --queue-delay-seconds or --requests-per-worker"
        )
    if delay is not None:
        if current.get("type") == "LOAD_BALANCER":
            raise click.UsageError(
                "load-balancer endpoints scale on --requests-per-worker"
            )
        patch["scaling"] = {"type": "QUEUE_DELAY", "queueDelay": delay}
    elif count is not None:
        patch["scaling"] = {"type": "REQUEST_COUNT", "requestCount": count}
    return patch


def config_body(options: dict, current: Optional[dict] = None) -> dict:
    """map flags to a create or partial update, preserving untouched configuration."""
    updating = current is not None
    current = current if updating else {"type": options["endpoint_type"]}
    body = {}
    if not updating:
        body["type"] = current["type"]
    for key, flag in (
        ("name", "name"),
        ("image", "image"),
        ("templateId", "template"),
        ("args", "args"),
        ("disk", "disk"),
        ("registry", "registry"),
    ):
        if options.get(flag) is not None:
            body[key] = options[flag]
    for key in ("entrypoint", "cmd"):
        if options.get(key) is not None:
            body[key] = shlex.split(options[key])
    if options.get("port"):
        body["ports"] = list(options["port"])
    if options.get("flashboot"):
        body["flashboot"] = FLASHBOOT[options["flashboot"].lower()]
    if options.get("request_timeout_seconds") is not None:
        body["timeout"] = options["request_timeout_seconds"] * 1000

    if options.get("env") or options.get("unset_env"):
        env = {**current.get("env", {}), **parse_env(options.get("env", ()))}
        for key in options.get("unset_env", ()):
            if key not in env:
                raise click.UsageError(f"--unset-env {key}: no such variable")
            del env[key]
        body["env"] = env

    if options.get("gpu") and options.get("cpu"):
        raise click.UsageError("use either --gpu or --cpu")
    if current.get("cpu") and options.get("gpu"):
        raise click.UsageError("a CPU endpoint cannot switch to GPUs")
    if current.get("gpu") and options.get("cpu"):
        raise click.UsageError("a GPU endpoint cannot switch to CPUs")
    if (options.get("cpu") or current.get("cpu")) and (
        options.get("cuda") or options.get("gpu_count")
    ):
        raise click.UsageError("--cuda and --gpu-count apply to GPU endpoints")
    gpu = _gpu(
        options.get("gpu"), options.get("gpu_count"), options.get("cuda"), updating
    )
    if gpu:
        body["gpu"] = gpu
    cpu = _cpu(options.get("cpu"), options.get("vcpu_count"), current)
    if cpu:
        body["cpu"] = cpu

    body.update(scale_patch(options, current))
    if not updating and "scaling" not in body:
        if current["type"] == "LOAD_BALANCER":
            raise click.UsageError("load-balancer endpoints need --requests-per-worker")
        body["scaling"] = {"type": "QUEUE_DELAY", "queueDelay": 4}
    places = (*options.get("data_center", ()), *options.get("region", ()))
    if places:
        body["dataCenterIds"] = compute.resolve_regions(
            places, compute.list_datacenters()
        )
    if options.get("network_volume") or options.get("global_volume"):
        mounts = dict(current.get("mounts", {}))
        for kind, flag in (("network", "network_volume"), ("global", "global_volume")):
            if options.get(flag):
                mounts[kind] = [{"volumeId": volume} for volume in options[flag]]
        body["mounts"] = mounts
    return body


def mutable(endpoint: dict) -> dict:
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


def validate(text: str, before: dict) -> dict:
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


def patch(before: dict, after: dict) -> dict:
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
