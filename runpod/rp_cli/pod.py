import time
from operator import itemgetter
from typing import Optional

import click

from runpod.api.rest import run_rest_request
from runpod.cli.groups.pod.commands import sync_pods
from runpod.rp_cli import compute
from runpod.rp_cli.api import api_errors, list_all
from runpod.rp_cli.options import container_fields
from runpod.rp_cli.output import (
    completer,
    confirm,
    interactive,
    json_option,
    pick,
    print_fields,
    print_json,
    print_table,
)

STATUSES = ["PROVISIONING", "STARTING", "RUNNING", "EXITED", "ERROR", "TERMINATED"]
DEFAULT_MOUNT_PATH = "/workspace"
WAIT_TIMEOUT = 600
WAIT_INTERVAL = 5


def _pod_choices(action: Optional[str] = None) -> list[tuple[str, str]]:
    return [
        (pod["id"], f"{pod['name']}  {pod['status'].lower()}")
        for pod in list_all("/v2/pods", "pods")
        if action is None or action in pod["actions"]
    ]


def _resolve_pod_id(pod_id: Optional[str], action: Optional[str] = None) -> str:
    if pod_id:
        return pod_id
    choices = []
    if interactive():
        with api_errors():
            choices = _pod_choices(action)
    return pick("pod", choices, "pass a POD_ID")


_complete_pods = completer(_pod_choices)
_complete_datacenters = completer(
    lambda: [
        (dc["id"], (dc.get("region") or "").lower())
        for dc in compute.list_datacenters()
    ]
)
_complete_network_volumes = completer(
    lambda: [
        (volume["id"], f"{volume.get('name')} {volume.get('dataCenter')}")
        for volume in list_all("/v2/network-volumes", "networkVolumes")
    ]
)
_complete_registries = completer(
    lambda: [
        (registry["name"], registry["id"])
        for registry in list_all("/v2/registries", "registries")
    ]
)


def _say(message: str, as_json: bool, warning: bool = False) -> None:
    if as_json:
        click.echo(message, err=True)
        return
    from rich.markup import escape

    from runpod.rp_cli import console as ui

    (ui.warn if warning else ui.info)(escape(message))


def _status(pod: dict) -> str:
    status = pod["status"].lower()
    if status == "running" and not (pod.get("runtime") or {}).get("uptime"):
        return "starting"
    return status


def _summary(pod: dict) -> dict:
    hardware = pod.get("gpu") or pod.get("cpu") or {}
    description = None
    if pod.get("gpu"):
        description = f"{hardware['count']}x {hardware['id']}"
    elif pod.get("cpu"):
        description = f"{hardware['id']} {hardware['vcpuCount']} vcpu"
    return {
        "id": pod["id"],
        "name": pod["name"],
        "status": _status(pod),
        "hardware": description,
        "data center": pod.get("dataCenterId"),
        "cost": f"${pod['cost']:.2f}/hr" if pod.get("cost") else None,
        "image": pod["image"],
    }


def _show(pod: dict, as_json: bool) -> None:
    if as_json:
        print_json(pod)
        return
    runtime = pod.get("runtime") or {}
    mounts = pod.get("mounts") or {}
    networking = pod.get("globalNetworking") or {}
    ssh = pod.get("ssh") or {}
    hardware = pod.get("gpu") or pod.get("cpu") or {}
    volumes = []
    if mounts.get("persistent"):
        volume = mounts["persistent"]
        volumes.append(f"persistent {volume['size']}GB at {volume['path']}")
    for kind in ("network", "global"):
        for volume in mounts.get(kind, []):
            volumes.append(
                f"{kind} {volume['volumeId']} at {volume.get('path') or '-'}"
            )
    uptime = None
    if runtime.get("uptime"):
        minutes, seconds = divmod(runtime["uptime"], 60)
        hours, minutes = divmod(minutes, 60)
        uptime = f"{hours}h {minutes}m" if hours else f"{minutes}m {seconds}s"
        if not hours and not minutes:
            uptime = f"{seconds}s"
    fields = {
        **_summary(pod),
        "resources": [
            f"{hardware[key]:g}{unit}"
            for key, unit in (("vcpuCount", " vcpu"), ("memory", "GB ram"))
            if hardware.get(key)
        ],
        "cloud": (pod.get("cloud") or "").lower(),
        "cuda": pod.get("cudaVersion"),
        "disk": f"{pod['disk']}GB" if pod.get("disk") else None,
        "volumes": volumes,
        "ports": pod.get("ports"),
        "env": sorted(pod.get("env") or {}),
        "command": pod.get("args"),
        "locked": pod.get("locked"),
        "global network": bool(networking.get("enabled"))
        and (networking.get("internalDns") or networking.get("ip") or True),
        "cluster": (pod.get("cluster") or {}).get("id"),
        "created": pod.get("createdAt"),
        "started": pod.get("startedAt"),
        "uptime": uptime,
        "actions": pod["actions"],
        "ssh": (ssh.get("proxy") or {}).get("command"),
        "ssh direct": (ssh.get("direct") or {}).get("command"),
        "urls": [
            f"https://{pod['id']}-{port.split('/')[0]}.proxy.runpod.net"
            for port in pod.get("ports", [])
            if port.endswith("/http")
        ],
        "tcp": [
            f"{port['ip']}:{port['public']} -> {port['private']}"
            for port in runtime.get("ports", [])
            if port["type"] == "tcp" and port.get("ip") and port.get("public")
        ],
    }
    print_fields(fields, [(label, itemgetter(label)) for label in fields])


def _wait_running(pod_id: str, since: float, previous: Optional[dict] = None) -> dict:
    from runpod.rp_cli import console as ui

    deadline = time.monotonic() + WAIT_TIMEOUT
    previous_uptime = ((previous or {}).get("runtime") or {}).get("uptime") or 0
    runtime_restarted = previous is None
    with ui.console.status(f"[dim]waiting for {pod_id} to start ...[/dim]"):
        while True:
            pod = run_rest_request("GET", f"/v2/pods/{pod_id}")
            status = _status(pod)
            uptime = pod["runtime"]["uptime"] if status == "running" else 0
            if status != "running" or uptime < previous_uptime:
                runtime_restarted = True
            new_start = previous is None or (
                pod.get("startedAt") and pod["startedAt"] != previous.get("startedAt")
            )
            if (
                status == "running"
                and new_start
                and runtime_restarted
                and uptime <= time.monotonic() - since + 1
            ):
                return pod
            if status in ("error", "terminated"):
                raise click.ClickException(f"pod {pod_id} is {status}")
            if time.monotonic() >= deadline:
                raise click.ClickException(
                    f"pod {pod_id} is still {status or 'unknown'} after {WAIT_TIMEOUT}s"
                )
            time.sleep(WAIT_INTERVAL)


def _container_body(
    options: dict, current: Optional[dict] = None, unset_env=()
) -> dict:
    """build container settings, preserving existing environment and mount paths."""
    current = current or {}
    body = container_fields(options, current, unset_env)
    mounts = {}
    for kind in ("persistent", "network", "global"):
        value = options[f"{kind}_volume"]
        if value is None:
            continue
        head, sep, path = value.partition(":")
        flag = f"--{kind}-volume"
        if not head or (sep and not path):
            raise click.BadParameter(
                f"'{value}' should look like VALUE[:PATH]", param_hint=flag
            )
        existing = current.get("mounts", {}).get(kind)
        if kind != "persistent":
            existing = existing[0] if existing else None
        path = path or (existing or {}).get("path")
        if kind != "global":
            path = path or DEFAULT_MOUNT_PATH
        if kind == "persistent":
            if not head.isdigit():
                raise click.BadParameter(
                    "size is in GB, like 50 or 50:/workspace", param_hint=flag
                )
            mounts[kind] = {"size": int(head), "path": path}
        else:
            mounts[kind] = [{"volumeId": head, **({"path": path} if path else {})}]
    if mounts:
        body["mounts"] = mounts
    return body


_CONTAINER_OPTIONS = [
    click.option(
        "--image", default=None, help="Container image, like runpod/pytorch:latest."
    ),
    click.option(
        "--template",
        default=None,
        help="Template id whose container settings to apply.",
    ),
    click.option(
        "--env",
        "-e",
        multiple=True,
        metavar="KEY=VALUE",
        help="Environment variable. Repeatable.",
    ),
    click.option(
        "--port",
        "-p",
        "ports",
        multiple=True,
        metavar="PORT/PROTO",
        help="Exposed port like 8888/http or 22/tcp. Repeatable.",
    ),
    click.option(
        "--disk", type=click.IntRange(min=1), default=None, help="Container disk in GB."
    ),
    click.option(
        "--persistent-volume",
        metavar="GB[:PATH]",
        default=None,
        help=f"Host-local persistent volume (path defaults to {DEFAULT_MOUNT_PATH}).",
    ),
    click.option(
        "--network-volume",
        metavar="ID[:PATH]",
        default=None,
        shell_complete=_complete_network_volumes,
        help=f"Network volume to mount (path defaults to {DEFAULT_MOUNT_PATH}).",
    ),
    click.option(
        "--global-volume",
        metavar="ID[:PATH]",
        default=None,
        help="Global volume to mount.",
    ),
    click.option(
        "--args",
        default=None,
        help="Container start command as one raw string ('' clears).",
    ),
    click.option(
        "--cmd",
        default=None,
        help="Container CMD as a shell string, split into exec form.",
    ),
    click.option(
        "--entrypoint",
        default=None,
        help="Container ENTRYPOINT as a shell string, split into exec form.",
    ),
    click.option(
        "--registry",
        default=None,
        shell_complete=_complete_registries,
        help="Registry credential name or id for private images ('' clears).",
    ),
    click.option(
        "--global-networking/--no-global-networking",
        default=None,
        help="Private networking across data centers (NVIDIA GPUs only).",
    ),
]


def _container_options(command):
    for option in reversed(_CONTAINER_OPTIONS):
        command = option(command)
    return command


def _gpu_body(
    spec: str,
    count: Optional[int],
    cloud: str,
    cuda: tuple[str, ...],
    datacenter_ids: list[str],
    min_ram: Optional[int],
    min_vcpu: Optional[int],
    as_json: bool,
) -> dict:
    """resolve a GPU specification by stock and price, then build its request."""
    query, spec_count = compute.parse_gpu_spec(spec)
    if spec_count and count and spec_count != count:
        raise click.UsageError(f"--gpu {spec} conflicts with --gpu-count {count}")
    count = spec_count or count or 1
    min_cuda, exact_cuda = compute.parse_cuda(cuda)
    gpus = compute.list_gpus(
        product="POD",
        count=count,
        cloud=cloud.upper(),
        cuda_versions=exact_cuda,
        min_cuda_version=min_cuda,
    )
    matches = compute.resolve_gpus(query, gpus)
    allowed = set(datacenter_ids)

    def stock(gpu: dict) -> str:
        if not allowed:
            return gpu.get("availability") or "NONE"
        return compute.best_availability(
            dc.get("availability")
            for dc in gpu.get("dataCenters") or []
            if dc["id"] in allowed
        )

    offered = [
        gpu
        for gpu in matches
        if len(matches) == 1
        or (
            gpu.get(cloud, True)
            and (gpu.get("maxCount") or {}).get(cloud, count) >= count
        )
    ]
    if not offered:
        names = ", ".join(gpu.get("name") or gpu["id"] for gpu in matches)
        raise compute.ResolutionError(
            f"no '{query}' gpu ({names}) offers {count} per pod on {cloud} cloud"
        )
    best = min(
        offered,
        key=lambda gpu: (
            -compute.availability_rank(stock(gpu)),
            (gpu.get("price") or {}).get(cloud) or float("inf"),
            gpu.get("name") or gpu["id"],
        ),
    )
    availability = stock(best)
    label = best.get("name") or best["id"]
    price = (best.get("price") or {}).get(cloud)
    if len(matches) > 1:
        cost = f", ${price * count:.2f}/hr" if price else ""
        _say(
            f"'{query}' matches {len(matches)} gpu types; using {label} "
            f"({best['id']}): {'no' if availability == 'NONE' else availability.lower()} stock{cost}",
            as_json,
        )
    if availability == "NONE":
        _say(
            f"the catalog reports no {cloud} stock for {count}x {label} "
            "in the requested data centers; creation may fail",
            as_json,
            warning=True,
        )
    body = {"id": best["id"], "count": count}
    for field, value in (
        ("minCudaVersion", min_cuda),
        ("allowedCudaVersions", exact_cuda),
        ("minRamPerGpu", min_ram),
        ("minVcpuCountPerGpu", min_vcpu),
    ):
        if value:
            body[field] = value
    return body


def _datacenter_ids(regions, data_centers, network_volume) -> list[str]:
    ids = (
        compute.resolve_regions(regions, compute.list_datacenters()) if regions else []
    )
    ids += [dc for dc in data_centers if dc not in ids]
    if not ids and network_volume:
        volume_id = network_volume.partition(":")[0]
        volume = run_rest_request("GET", f"/v2/network-volumes/{volume_id}")
        ids = [volume["dataCenter"]]
    return ids


def _create_body(
    name,
    gpu,
    gpu_count,
    cpu,
    vcpu,
    cloud,
    regions,
    data_centers,
    cuda,
    min_ram_per_gpu,
    min_vcpu_per_gpu,
    ssh,
    jupyter,
    as_json,
    **container,
):
    if gpu and cpu:
        raise click.UsageError("use either --gpu or --cpu")
    if not gpu and not cpu:
        raise click.UsageError("pass --gpu (like 4090 or h100:2) or --cpu (like cpu3c)")
    if cpu and (gpu_count or cuda or min_ram_per_gpu or min_vcpu_per_gpu):
        raise click.UsageError(
            "--gpu-count, --cuda, --min-ram-per-gpu, and --min-vcpu-per-gpu apply to gpu pods"
        )
    if vcpu and not cpu:
        raise click.UsageError("--vcpu applies to cpu pods")
    if not container["image"] and not container["template"]:
        raise click.UsageError("pass --image or --template")
    if not name:
        if not interactive():
            raise click.UsageError("pass a NAME")
        name = click.prompt("pod name")

    body = {"name": name, **_container_body(container)}
    datacenter_ids = _datacenter_ids(regions, data_centers, container["network_volume"])
    if datacenter_ids:
        body["dataCenterIds"] = datacenter_ids
    if cloud:
        body["cloud"] = cloud.upper()

    if gpu:
        body["gpu"] = _gpu_body(
            gpu,
            gpu_count,
            (cloud or "secure").lower(),
            cuda,
            datacenter_ids,
            min_ram_per_gpu,
            min_vcpu_per_gpu,
            as_json,
        )
    else:
        flavor = compute.resolve_cpu(
            cpu, compute.list_cpus(product="POD", vcpu_count=vcpu)
        )
        body["cpu"] = {
            "id": flavor["id"],
            "vcpuCount": vcpu or (flavor.get("vcpu") or {}).get("min") or 2,
        }

    if ssh:
        body["startSsh"] = True
        keys = run_rest_request("GET", "/v2/account/ssh-keys")["keys"]
        if not keys:
            _say(
                "your account has no ssh keys, so --ssh has no effect. "
                "add one with rp ssh add",
                as_json,
                warning=True,
            )
    if jupyter:
        body["startJupyter"] = True

    return body


@click.group("pod")
def pod():
    """Create and manage pods."""


@pod.command(name="list")
@click.option("--name", default=None, help="Only pods whose name contains this text.")
@click.option(
    "--status",
    "statuses",
    multiple=True,
    type=click.Choice(STATUSES, case_sensitive=False),
    help="Only pods in this status. Repeatable.",
)
@click.option(
    "--include-cluster-pods", is_flag=True, help="Include cluster member pods."
)
@json_option
def list_pods(name, statuses, include_cluster_pods, as_json):
    """List your pods."""
    with api_errors():
        params = {"includeClusterPods": "true"} if include_cluster_pods else None
        pods = list_all("/v2/pods", "pods", params)
    if name:
        pods = [p for p in pods if name.lower() in (p.get("name") or "").lower()]
    if statuses:
        wanted = {status.lower() for status in statuses}
        pods = [p for p in pods if _status(p) in wanted]
    if as_json:
        print_json(pods)
        return
    columns = ["id", "name", "status", "hardware", "data center", "cost", "image"]
    print_table(
        [_summary(p) for p in pods],
        [(label.upper(), itemgetter(label)) for label in columns],
        "no pods. create one with rp pod create",
    )


@pod.command()
@click.argument("pod_id", required=False, shell_complete=_complete_pods)
@json_option
def get(pod_id, as_json):
    """Show a pod with its connection details."""
    pod_id = _resolve_pod_id(pod_id)
    with api_errors():
        _show(run_rest_request("GET", f"/v2/pods/{pod_id}"), as_json)


@pod.command()
@click.argument("name", required=False)
@click.option(
    "--gpu", default=None, metavar="TYPE[:COUNT]", help="GPU type like 4090 or h100:2."
)
@click.option(
    "--gpu-count", type=click.IntRange(min=1), default=None, help="GPUs per pod."
)
@click.option(
    "--cpu", default=None, help="CPU flavor like cpu3c or cpu5g, for a CPU pod."
)
@click.option(
    "--vcpu",
    type=click.IntRange(min=2),
    default=None,
    help="vCPUs for a CPU pod (defaults to the flavor's minimum).",
)
@click.option(
    "--cloud",
    type=click.Choice(["secure", "community"], case_sensitive=False),
    default=None,
    help="Cloud tier (the api defaults to secure).",
)
@click.option(
    "--region",
    "regions",
    multiple=True,
    help="Region (eu, na, asia), id prefix (us, eu-ro), or data center id. Repeatable.",
)
@click.option(
    "--data-center",
    "data_centers",
    multiple=True,
    shell_complete=_complete_datacenters,
    help="Exact data center id like US-TX-3. Repeatable.",
)
@click.option(
    "--cuda",
    multiple=True,
    help="Host CUDA: '>=12.4' for a floor, 12.4 or 12.4,12.8 for exact versions.",
)
@click.option(
    "--min-ram-per-gpu",
    type=click.IntRange(min=1),
    default=None,
    help="Minimum host RAM in GB per GPU.",
)
@click.option(
    "--min-vcpu-per-gpu",
    type=click.IntRange(min=1),
    default=None,
    help="Minimum host vCPUs per GPU.",
)
@_container_options
@click.option("--ssh", is_flag=True, help="Start sshd with your account's SSH keys.")
@click.option(
    "--jupyter", is_flag=True, help="Start JupyterLab (expose 8888/http to reach it)."
)
@click.option("--wait", is_flag=True, help="Wait until the pod is running.")
@json_option
def create(name, wait, as_json, **options):
    """Create a pod named NAME.

    GPU types are human-readable (4090, h100, a100-sxm). When a name
    matches several types, the one with the most stock in the requested
    regions wins, then the cheapest.

    \b
      rp pod create trainer --gpu h100:2 --image runpod/pytorch:latest --region eu --ssh
      rp pod create box --cpu cpu3c --vcpu 4 --image ubuntu:24.04
      rp pod create nb --gpu 4090 --image runpod/pytorch:latest --jupyter -p 8888/http
    """
    with api_errors():
        body = _create_body(name, as_json=as_json, **options)
        since = time.monotonic()
        created = run_rest_request("POST", "/v2/pods", json=body)
        _say(f"created pod {created['name']} ({created['id']})", as_json)
        if wait:
            created = _wait_running(created["id"], since)
    _show(created, as_json)


@pod.command()
@click.argument("pod_id", required=False, shell_complete=_complete_pods)
@click.option("--name", default=None, help="New pod name.")
@_container_options
@click.option(
    "--unset-env",
    multiple=True,
    metavar="KEY",
    help="Remove an environment variable. Repeatable.",
)
@click.option(
    "--lock/--unlock",
    "locked",
    default=None,
    help="Lock the pod against stop and reset.",
)
@json_option
def update(pod_id, name, unset_env, locked, as_json, **container):
    """Change a pod's settings.

    --env and --unset-env edit the existing environment, --port replaces
    the exposed ports, and volume flags restate the full mount (the
    volume id cannot change; the path defaults to the current one).
    Most changes apply on the pod's next start.
    """
    requested = [name, locked, *unset_env, *container.values()]
    if all(value in (None, ()) for value in requested):
        raise click.UsageError("nothing to update. see rp pod update --help")
    pod_id = _resolve_pod_id(pod_id)

    with api_errors():
        needs_current = (
            container["env"]
            or unset_env
            or any(
                container[key]
                for key in ("persistent_volume", "network_volume", "global_volume")
            )
        )
        current = (
            run_rest_request("GET", f"/v2/pods/{pod_id}") if needs_current else None
        )
        body = _container_body(container, current, unset_env)
        if name:
            body["name"] = name
        if locked is not None:
            body["locked"] = locked
        updated = run_rest_request("PATCH", f"/v2/pods/{pod_id}", json=body)
    _say(f"updated pod {pod_id}", as_json)
    _show(updated, as_json)


def _act(
    pod_id: Optional[str], action: str, done: str, as_json: bool, wait: bool = False
):
    pod_id = _resolve_pod_id(pod_id, action)
    with api_errors():
        previous = (
            run_rest_request("GET", f"/v2/pods/{pod_id}")
            if wait and action == "restart"
            else None
        )
        since = time.monotonic()
        result = run_rest_request(
            "POST", f"/v2/pods/{pod_id}/action", json={"action": action}
        )
        _say(f"{done} pod {pod_id}", as_json)
        if wait:
            result = _wait_running(pod_id, since, previous)
    _show(result, as_json)


@pod.command()
@click.argument("pod_id", required=False, shell_complete=_complete_pods)
@click.option("--wait", is_flag=True, help="Wait until the pod is running.")
@json_option
def start(pod_id, wait, as_json):
    """Start a stopped pod."""
    _act(pod_id, "start", "started", as_json, wait)


@pod.command()
@click.argument("pod_id", required=False, shell_complete=_complete_pods)
@json_option
def stop(pod_id, as_json):
    """Stop a running pod, keeping its volume."""
    _act(pod_id, "stop", "stopped", as_json)


@pod.command()
@click.argument("pod_id", required=False, shell_complete=_complete_pods)
@click.option("--wait", is_flag=True, help="Wait until the pod is running.")
@json_option
def restart(pod_id, wait, as_json):
    """Restart a running pod."""
    _act(pod_id, "restart", "restarted", as_json, wait)


@pod.command()
@click.argument("pod_id", required=False, shell_complete=_complete_pods)
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
def terminate(pod_id, yes):
    """Terminate a pod permanently."""
    from runpod.rp_cli import console as ui

    pod_id = _resolve_pod_id(pod_id, "terminate")
    confirm(f"terminate pod {pod_id}? its disk and persistent volume are deleted", yes)
    with api_errors():
        run_rest_request("DELETE", f"/v2/pods/{pod_id}")
    ui.success(f"terminated pod {pod_id}")


pod.add_command(terminate, "delete")


@pod.command()
@click.argument("pod_id", required=False, shell_complete=_complete_pods)
@click.option("--follow", "-f", is_flag=True, help="Follow new log lines.")
@click.option("--tail", type=click.IntRange(0, 5000), default=100, show_default=True)
@click.option("--source", type=click.Choice(["container", "system"]))
@click.option("--since", help="Only logs after this timestamp.")
@json_option
def logs(pod_id, follow, tail, source, since, as_json):
    """Read a pod's container or system logs."""
    from runpod.rp_cli.logs import show_logs

    pod_id = _resolve_pod_id(pod_id)
    show_logs(
        pod_id,
        follow=follow,
        tail=tail,
        source=source,
        since=since,
        as_json=as_json,
    )


@pod.command()
@click.argument("pod_id", required=False, shell_complete=_complete_pods)
def connect(pod_id):
    """Open an interactive terminal on a pod over SSH.

    Needs an SSH key on your account (rp ssh add) and 22/tcp exposed.
    """
    from runpod.cli.utils import ssh_cmd

    pod_id = _resolve_pod_id(pod_id)
    click.echo(f"connecting to pod {pod_id}...")
    try:
        ssh = ssh_cmd.SSHConnection(pod_id)
    except (ValueError, TimeoutError) as exc:
        raise click.ClickException(str(exc)) from exc
    ssh.launch_terminal()


pod.add_command(sync_pods, "sync")
