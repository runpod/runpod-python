"""`rp pod`: create, inspect, and manage pods."""

import shlex
import time
from typing import Optional

import click

from runpod.api.rest import run_rest_request
from runpod.cli.groups.pod.commands import sync_pods
from runpod.rp_cli import compute
from runpod.rp_cli.output import (
    api_errors,
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


def _list_pods(include_cluster_pods: bool = False) -> list[dict]:
    params = {"includeClusterPods": "true"} if include_cluster_pods else None
    return compute.list_all("/v2/pods", "pods", params)


def _get_pod(pod_id: str) -> dict:
    return run_rest_request("GET", f"/v2/pods/{pod_id}") or {}


def _pod_choices(action: Optional[str] = None) -> list[tuple[str, str]]:
    return [
        (pod["id"], f"{pod.get('name')}  {(pod.get('status') or '').lower()}")
        for pod in _list_pods()
        if action is None or action in (pod.get("actions") or [])
    ]


def _resolve_pod_id(pod_id: Optional[str], action: Optional[str] = None) -> str:
    """the given pod id, or one picked interactively."""
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
        for volume in compute.list_all("/v2/network-volumes", "networkVolumes")
    ]
)
_complete_registries = completer(
    lambda: [
        (registry["name"], registry["id"])
        for registry in compute.list_all("/v2/registries", "registries")
    ]
)


def _say(message: str, as_json: bool, warning: bool = False) -> None:
    """a status line on stdout, or stderr when stdout carries json."""
    if as_json:
        click.echo(message, err=True)
        return
    from rich.markup import escape

    from runpod.rp_cli import console as ui

    (ui.warn if warning else ui.info)(escape(message))


def _hardware(pod: dict) -> Optional[str]:
    gpu = pod.get("gpu")
    if gpu:
        return f"{gpu.get('count') or 1}x {gpu['id']}"
    cpu = pod.get("cpu")
    if cpu:
        return f"{cpu['id']} {cpu.get('vcpuCount')} vcpu"
    return None


def _cost(pod: dict) -> Optional[str]:
    return f"${pod['cost']:.2f}/hr" if pod.get("cost") else None


def _mounts(pod: dict) -> list[str]:
    mounts = pod.get("mounts") or {}
    described = []
    persistent = mounts.get("persistent")
    if persistent:
        described.append(f"persistent {persistent['size']}GB at {persistent['path']}")
    for mount in mounts.get("network") or []:
        described.append(f"network {mount['volumeId']} at {mount.get('path')}")
    for mount in mounts.get("global") or []:
        described.append(f"global {mount['volumeId']} at {mount.get('path') or '-'}")
    return described


def _urls(pod: dict) -> list[str]:
    urls = []
    for port in pod.get("ports") or []:
        number, _, protocol = port.partition("/")
        if protocol == "http":
            urls.append(f"https://{pod['id']}-{number}.proxy.runpod.net")
    return urls


def _tcp(pod: dict) -> list[str]:
    ports = (pod.get("runtime") or {}).get("ports") or []
    return [
        f"{port['ip']}:{port['public']} -> {port['private']}"
        for port in ports
        if port.get("type") == "tcp" and port.get("ip") and port.get("public")
    ]


def _global_network(pod: dict) -> object:
    networking = pod.get("globalNetworking") or {}
    if not networking.get("enabled"):
        return False
    return networking.get("internalDns") or networking.get("ip") or True


def _resources(pod: dict) -> Optional[str]:
    hardware = pod.get("gpu") or pod.get("cpu") or {}
    parts = []
    if hardware.get("vcpuCount"):
        parts.append(f"{hardware['vcpuCount']:g} vcpu")
    if hardware.get("memory"):
        parts.append(f"{hardware['memory']:g}GB ram")
    return ", ".join(parts) or None


_LIST_COLUMNS = [
    ("ID", lambda pod: pod["id"]),
    ("NAME", lambda pod: pod.get("name")),
    ("STATUS", lambda pod: (pod.get("status") or "").lower()),
    ("HARDWARE", _hardware),
    ("DATA CENTER", lambda pod: pod.get("dataCenterId")),
    ("COST", _cost),
    ("IMAGE", lambda pod: pod.get("image")),
]

_FIELDS = [
    ("id", lambda pod: pod["id"]),
    ("name", lambda pod: pod.get("name")),
    ("status", lambda pod: (pod.get("status") or "").lower()),
    ("image", lambda pod: pod.get("image")),
    ("hardware", _hardware),
    ("resources", _resources),
    ("cloud", lambda pod: (pod.get("cloud") or "").lower()),
    ("data center", lambda pod: pod.get("dataCenterId")),
    ("cuda", lambda pod: pod.get("cudaVersion")),
    ("cost", _cost),
    ("disk", lambda pod: f"{pod['disk']}GB" if pod.get("disk") else None),
    ("volumes", _mounts),
    ("ports", lambda pod: pod.get("ports")),
    ("env", lambda pod: sorted(pod.get("env") or {})),
    ("command", lambda pod: pod.get("args")),
    ("locked", lambda pod: pod.get("locked")),
    ("global network", _global_network),
    ("cluster", lambda pod: (pod.get("cluster") or {}).get("id")),
    ("created", lambda pod: pod.get("createdAt")),
    ("started", lambda pod: pod.get("startedAt")),
    ("actions", lambda pod: pod.get("actions")),
    ("ssh", lambda pod: ((pod.get("ssh") or {}).get("proxy") or {}).get("command")),
    (
        "ssh direct",
        lambda pod: ((pod.get("ssh") or {}).get("direct") or {}).get("command"),
    ),
    ("urls", _urls),
    ("tcp", _tcp),
]


def _show(pod: dict, as_json: bool) -> None:
    if as_json:
        print_json(pod)
        return
    print_fields(pod, _FIELDS)


def _wait_running(pod_id: str) -> dict:
    """poll a pod until it is running."""
    from runpod.rp_cli import console as ui

    deadline = time.monotonic() + WAIT_TIMEOUT
    with ui.console.status(f"[dim]waiting for {pod_id} to start ...[/dim]"):
        while True:
            pod = _get_pod(pod_id)
            status = pod.get("status")
            if status == "RUNNING":
                return pod
            if status in ("ERROR", "TERMINATED"):
                raise click.ClickException(f"pod {pod_id} is {status.lower()}")
            if time.monotonic() >= deadline:
                raise click.ClickException(
                    f"pod {pod_id} is still {(status or 'unknown').lower()} "
                    f"after {WAIT_TIMEOUT}s"
                )
            time.sleep(WAIT_INTERVAL)


def _parse_env(values: tuple[str, ...]) -> dict[str, str]:
    env = {}
    for value in values:
        key, sep, setting = value.partition("=")
        if not sep or not key:
            raise click.BadParameter(f"'{value}' is not KEY=VALUE", param_hint="--env")
        env[key] = setting
    return env


def _split_mount(value: str, flag: str) -> tuple[str, Optional[str]]:
    head, sep, path = value.partition(":")
    if not head or (sep and not path):
        raise click.BadParameter(
            f"'{value}' should look like VALUE[:PATH]", param_hint=flag
        )
    return head, path or None


def _mount_path(current: dict, kind: str) -> Optional[str]:
    entries = current.get(kind) or []
    return entries[0].get("path") if entries else None


def _mounts_body(options: dict, current: dict) -> dict:
    """the mounts object for the volume flags, defaulting paths from current."""
    mounts: dict = {}
    if options["persistent_volume"]:
        size, path = _split_mount(options["persistent_volume"], "--persistent-volume")
        if not size.isdigit():
            raise click.BadParameter(
                "size is in GB, like 50 or 50:/workspace",
                param_hint="--persistent-volume",
            )
        mounts["persistent"] = {
            "size": int(size),
            "path": path
            or (current.get("persistent") or {}).get("path")
            or DEFAULT_MOUNT_PATH,
        }
    if options["network_volume"]:
        volume_id, path = _split_mount(options["network_volume"], "--network-volume")
        mounts["network"] = [
            {
                "volumeId": volume_id,
                "path": path or _mount_path(current, "network") or DEFAULT_MOUNT_PATH,
            }
        ]
    if options["global_volume"]:
        volume_id, path = _split_mount(options["global_volume"], "--global-volume")
        path = path or _mount_path(current, "global")
        mounts["global"] = [{"volumeId": volume_id, **({"path": path} if path else {})}]
    return mounts


def _resolve_registry(value: str) -> Optional[str]:
    if not value:
        return None
    for registry in compute.list_all("/v2/registries", "registries"):
        if value in (registry["id"], registry.get("name")):
            return registry["id"]
    raise compute.ResolutionError(
        f"unknown registry credential '{value}'. list them with rp registry list"
    )


def _container_body(options: dict) -> dict:
    """request fields shared by create and update, except env and mounts."""
    body: dict = {}
    if options["image"]:
        body["image"] = options["image"]
    if options["template"]:
        body["templateId"] = options["template"]
    if options["args"] is not None:
        body["args"] = options["args"]
    if options["cmd"] is not None:
        body["cmd"] = shlex.split(options["cmd"])
    if options["entrypoint"] is not None:
        body["entrypoint"] = shlex.split(options["entrypoint"])
    if options["disk"]:
        body["disk"] = options["disk"]
    if options["ports"]:
        body["ports"] = list(options["ports"])
    if options["registry"] is not None:
        body["registry"] = _resolve_registry(options["registry"])
    if options["global_networking"] is not None:
        body["globalNetworking"] = options["global_networking"]
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


def _choose_gpu(
    query: str,
    count: int,
    cloud: str,
    cuda: tuple[Optional[str], list[str]],
    datacenter_ids: list[str],
) -> tuple[dict, str, int]:
    """the gpu type to request, its stock level, and how many types matched.

    a query naming several types picks the one with the most stock in the
    requested data centers, then the lowest price.
    """
    min_cuda, exact_cuda = cuda
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
    return best, stock(best), len(matches)


def _datacenter_ids(regions, data_centers, network_volume) -> list[str]:
    ids = (
        compute.resolve_regions(regions, compute.list_datacenters()) if regions else []
    )
    ids += [dc for dc in data_centers if dc not in ids]
    if not ids and network_volume:
        volume_id = network_volume.partition(":")[0]
        volume = run_rest_request("GET", f"/v2/network-volumes/{volume_id}") or {}
        if volume.get("dataCenter"):
            ids = [volume["dataCenter"]]
    return ids


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
        pods = _list_pods(include_cluster_pods)
    if name:
        pods = [p for p in pods if name.lower() in (p.get("name") or "").lower()]
    if statuses:
        wanted = {status.upper() for status in statuses}
        pods = [p for p in pods if p.get("status") in wanted]
    if as_json:
        print_json(pods)
        return
    print_table(pods, _LIST_COLUMNS, "no pods. create one with rp pod create")


@pod.command()
@click.argument("pod_id", required=False, shell_complete=_complete_pods)
@json_option
def get(pod_id, as_json):
    """Show a pod with its connection details."""
    pod_id = _resolve_pod_id(pod_id)
    with api_errors():
        _show(_get_pod(pod_id), as_json)


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
def create(
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
    wait,
    as_json,
    **container,
):  # pylint: disable=too-many-arguments,too-many-locals
    """Create a pod named NAME.

    GPU types are human-readable (4090, h100, a100-sxm). When a name
    matches several types, the one with the most stock in the requested
    regions wins, then the cheapest.

    \b
      rp pod create trainer --gpu h100:2 --image runpod/pytorch:latest --region eu --ssh
      rp pod create box --cpu cpu3c --vcpu 4 --image ubuntu:24.04
      rp pod create nb --gpu 4090 --image runpod/pytorch:latest --jupyter -p 8888/http
    """
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

    with api_errors():
        body = {"name": name, **_container_body(container)}
        env = _parse_env(container["env"])
        if env:
            body["env"] = env
        mounts = _mounts_body(container, {})
        if mounts:
            body["mounts"] = mounts
        datacenter_ids = _datacenter_ids(
            regions, data_centers, container["network_volume"]
        )
        if datacenter_ids:
            body["dataCenterIds"] = datacenter_ids
        if cloud:
            body["cloud"] = cloud.upper()

        if gpu:
            query, spec_count = compute.parse_gpu_spec(gpu)
            if spec_count and gpu_count and spec_count != gpu_count:
                raise click.UsageError(
                    f"--gpu {gpu} conflicts with --gpu-count {gpu_count}"
                )
            count = spec_count or gpu_count or 1
            min_cuda, exact_cuda = compute.parse_cuda(cuda)
            tier = (cloud or "secure").lower()
            chosen, stock, matched = _choose_gpu(
                query, count, tier, (min_cuda, exact_cuda), datacenter_ids
            )
            label = chosen.get("name") or chosen["id"]
            price = (chosen.get("price") or {}).get(tier)
            if matched > 1:
                cost = f", ${price * count:.2f}/hr" if price else ""
                _say(
                    f"'{query}' matches {matched} gpu types; using {label} "
                    f"({chosen['id']}): {'no' if stock == 'NONE' else stock.lower()} stock{cost}",
                    as_json,
                )
            if stock == "NONE":
                _say(
                    f"the catalog reports no {tier} stock for {count}x {label} "
                    "in the requested data centers; creation may fail",
                    as_json,
                    warning=True,
                )
            gpu_body = {"id": chosen["id"], "count": count}
            if min_cuda:
                gpu_body["minCudaVersion"] = min_cuda
            if exact_cuda:
                gpu_body["allowedCudaVersions"] = exact_cuda
            if min_ram_per_gpu:
                gpu_body["minRamPerGpu"] = min_ram_per_gpu
            if min_vcpu_per_gpu:
                gpu_body["minVcpuCountPerGpu"] = min_vcpu_per_gpu
            body["gpu"] = gpu_body
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
            keys = (run_rest_request("GET", "/v2/account/ssh-keys") or {}).get("keys")
            if not keys:
                _say(
                    "your account has no ssh keys, so --ssh has no effect. "
                    "add one with rp ssh add",
                    as_json,
                    warning=True,
                )
        if jupyter:
            body["startJupyter"] = True

        created = run_rest_request("POST", "/v2/pods", json=body) or {}
        _say(f"created pod {created.get('name')} ({created.get('id')})", as_json)
        if wait:
            created = _wait_running(created["id"])
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
        body = _container_body(container)
        if name:
            body["name"] = name
        if locked is not None:
            body["locked"] = locked
        mounts_changed = any(
            container[key]
            for key in ("persistent_volume", "network_volume", "global_volume")
        )
        if container["env"] or unset_env or mounts_changed:
            current = _get_pod(pod_id)
            if container["env"] or unset_env:
                env = {**(current.get("env") or {}), **_parse_env(container["env"])}
                for key in unset_env:
                    env.pop(key, None)
                body["env"] = env
            if mounts_changed:
                body["mounts"] = _mounts_body(container, current.get("mounts") or {})
        updated = run_rest_request("PATCH", f"/v2/pods/{pod_id}", json=body) or {}
    _say(f"updated pod {pod_id}", as_json)
    _show(updated, as_json)


def _act(
    pod_id: Optional[str], action: str, done: str, as_json: bool, wait: bool = False
):
    pod_id = _resolve_pod_id(pod_id, action)
    with api_errors():
        result = run_rest_request(
            "POST", f"/v2/pods/{pod_id}/action", json={"action": action}
        )
        _say(f"{done} pod {pod_id}", as_json)
        if wait:
            result = _wait_running(pod_id)
    _show(result or {}, as_json)


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
