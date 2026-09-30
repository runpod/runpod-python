<div align="center">

<h1>Runpod Python</h1>

**Define GPU functions in Python. Run them in the cloud with one decorator.**

[![PyPI Package](https://badge.fury.io/py/runpod.svg)](https://badge.fury.io/py/runpod)
[![Downloads](https://static.pepy.tech/personalized-badge/runpod?period=total&units=international_system&left_color=grey&right_color=blue&left_text=downloads)](https://pepy.tech/project/runpod)
[![CI | Unit Tests](https://github.com/runpod/runpod-python/actions/workflows/CI-pytests.yml/badge.svg)](https://github.com/runpod/runpod-python/actions/workflows/CI-pytests.yml)
[![License](https://img.shields.io/badge/License-MIT-blue)](LICENSE)

[Documentation](https://docs.runpod.io) • [Examples](examples/apps) • [Discord](https://discord.gg/pJ3P2DbUUq)

</div>

## Installation

```bash
pip install runpod    # or: uv add runpod
rp login              # authenticate once
```

Requires Python 3.10+. Installing the package also installs the `rp` CLI.

## Example

```python
import runpod
from runpod import App, Model, NetworkVolume, Secret

app = App("inference")

models = NetworkVolume("models", size=100)
llama = Model("meta-llama/Llama-3.1-8B-Instruct")


# an autoscaling job queue on cloud H100s: weights pre-cached,
# dependencies vendored at deploy time, scale-to-zero when idle
@app.queue(
    gpu="H100",
    workers=(0, 3),
    dependencies=["vllm"],
    mounts={"/runpod-volume": models},
    model=llama,
    env={"HF_TOKEN": Secret("hf-token")},
)
def chat(prompt: str):
    import vllm

    llm = vllm.LLM(model=str(llama.path))   # weights already on disk
    return llm.generate(prompt)


# one ephemeral pod per call: provisions, runs to completion, terminates
@app.task(gpu="H100", gpu_count=2, mounts={"/models": models})
def finetune(steps: int = 1000):
    ...
    return {"loss": final_loss}


@runpod.local_entrypoint
def main():
    print(chat.remote("why is the sky blue?"))   # blocks for the result
    job = finetune.spawn(steps=500)              # fire and forget -> Job
```

```bash
rp flash dev main.py    # live dev session: edit, re-run, logs stream back
rp flash deploy         # deploy production endpoints
```

Functions keep their Python identity: `chat.remote(...)` runs in the cloud, `await chat.remote.aio(...)` is the async form, `chat.local(...)` runs in-process. See [`examples/apps`](examples/apps) for runnable examples and [docs.runpod.io](https://docs.runpod.io) for the full guide.

Queue `.remote()` calls request a synchronous result and poll the same job if the server's wait window expires. Fast results need no client-side polling. Dev sessions use a short sync window to keep worker logs responsive. Use `.spawn()` for an asynchronous job handle. A transport failure before receiving a job ID raises an error without resubmitting the work.

## Storage

`NetworkVolume(name_or_id, size=50, datacenter=None, create=True)` references
datacenter-local storage. Apps resolve names and choose a datacenter compatible
with every resource sharing the volume. `GlobalVolume(name_or_id, create=True)`
references global storage without a datacenter constraint. Both inherit from
the abstract `Volume` base, resolve by name or ID, and create missing storage
when provisioning remote compute. Set `create=False` to require existing storage.
Global-volume lookup and creation use GraphQL; network volumes use REST.

Declare attachments with `mounts={"/path": volume}`. Tasks support one network
and one global volume at distinct, non-overlapping paths. Queue and API resources
support one volume at `/runpod-volume`; global storage requires a GPU endpoint.

Inside worker code, `volume.path` returns the configured mount path. The runtime
binds declared references and resolved IDs before importing user code. Access
raises if the volume is unmounted or has multiple bindings. Evaluate `.path`
inside remote functions, not during local module discovery. Calling `.local()`
does not mount remote storage on the client machine.

## Sandboxes

Sandboxes are an access-controlled preview. An authorized account and a
sandbox-enabled API environment are required. Configure its API origin with
`RUNPOD_API_BASE_URL` or the handle's `base_url` option. Authenticate with
`RUNPOD_API_KEY`, `runpod.api_key`, or a handle-specific `api_key`.

Supply exactly one of `image` or `template_id`. Construction is local; entering
an owned context creates the sandbox and leaving it terminates remote compute.

```python
from runpod import Sandbox

with Sandbox(
    image="python:3.12-slim",
    cmd=["sleep", "infinity"],
    disk_gb=20,
    idle_timeout_seconds=300,
    max_lifetime_seconds=1200,
) as sandbox:
    result = sandbox.exec(["python", "-c", "print('hello from a sandbox')"], check=True)
    print(result.output)
    sandbox.extend(seconds=300)
    sandbox.update(idle_timeout_seconds=600)
```

Use `AsyncioSandbox` with awaited factory, execution, and lifecycle methods:

```python
import asyncio
from runpod import AsyncioSandbox

async def main():
    async with AsyncioSandbox(image="python:3.12-slim", cmd=["sleep", "infinity"]) as sandbox:
        result = await sandbox.exec(["python", "-c", "print('hello')"], check=True)
        print(result.output)

asyncio.run(main())
```

Create options include:

- `mounts={"/data": NetworkVolume("volume-id", create=False),
  "/models": GlobalVolume("global-volume-id")}` for one volume of each kind.
  Paths must be absolute and non-overlapping. Network storage pins placement to
  its datacenter; `data_center_ids` must include that datacenter if supplied.
  Creating a named network volume outside an app requires its explicit
  `datacenter`. Sandbox contexts never mount remote storage on the client.
- `ports={3000: "http", 3001: "tcp", 53: ("tcp", "udp")}` for exposed ports.
  TCP and UDP exposure depends on backend public-port support.
- `cmd` and `entrypoint` as argument lists, `registry_auth` as a saved credential
  name or ID, and `start_ssh=True` for SSH setup. SSH also requires registered
  keys and an image supporting that setup.

Omitted fields stay omitted. The SDK sends `mounts={}`, `ports={}`, and empty
command lists explicitly, preserving the distinction from absent configuration.
The container stops when its main command exits.

`Sandbox.create(...)` returns an owned handle. `get(...)` and `list(...)` return
borrowed handles whose contexts only close local connections. `terminate()`
releases remote compute; `close()` releases local resources and permits reuse.

`info`, `state`, `compute`, and `expires_at` are cached snapshots. `refresh()`
fetches a new snapshot, including mount, port, SSH, and placement metadata.
Unavailable configuration remains `None`, distinct from empty configuration.

`idle_timeout_seconds` measures inactivity; `max_lifetime_seconds` measures total
age from creation. `update(...)` changes only supplied lifetime settings.
`extend(seconds=...)` adds to the current lifetime after refreshing it. Calls
through one handle are serialized; independent clients can race because the
backend extension contract accepts an absolute lifetime.

`exec` returns combined output plus available `stdout`, `stderr`, `exit_code`,
`duration_ms`, and `truncated` details. `check=True` raises
`SandboxExecutionError` on a reported command failure, retaining the result.
Only explicit `sandbox_starting` conflicts are retried within `startup_timeout`.
Other conflicts, malformed responses, and ambiguous transport failures propagate.

`logs(source="container", tail=10)` streams main-process logs; `source="system"`
selects lifecycle events. Exec output is returned by `exec`. Use `with` and
regular iteration for synchronous streams, or `async with` and `async for` for
asynchronous streams. Save an event's `id` as `last_event_id` to resume, or filter
with `since`.

These features require backend support for the sandbox create, exec, and lifetime
contracts, plus compatible SDK/runtime releases for apps worker mount access.

## Contributing

Pull requests and issues are welcome — see the [contributing guide](CONTRIBUTING.md) to get started.

```bash
git clone https://github.com/runpod/runpod-python.git
cd runpod-python
make setup
make test
```

## ⚡ | Serverless Worker (SDK)

This python package can also be used to create a serverless worker that can be deployed to Runpod as a custom endpoint API.

### Quick Start

Create a python script in your project that contains your model definition and the Runpod worker start code. Run this python code as your default container start command:

```python
# my_worker.py

import runpod

def is_even(job):

    job_input = job["input"]
    the_number = job_input["number"]

    if not isinstance(the_number, int):
        return {"error": "Silly human, you need to pass an integer."}

    if the_number % 2 == 0:
        return True

    return False

runpod.serverless.start({"handler": is_even})
```

Make sure that this file is ran when your container starts. This can be accomplished by calling it in the docker command when you set up a template at [console.runpod.io/serverless/user/templates](https://console.runpod.io/serverless/user/templates) or by setting it as the default command in your Dockerfile.

See our [blog post](https://www.runpod.io/blog/build-basic-serverless-api) for creating a basic Serverless API, or view the [details docs](https://docs.runpod.io/serverless-ai/custom-apis) for more information.

### Local Test Worker

You can also test your worker locally before deploying it to Runpod. This is useful for debugging and testing.

```bash
python my_worker.py --rp_serve_api
```

### Worker Fitness Checks

Fitness checks allow you to validate your worker environment at startup before processing jobs. If any check fails, the worker exits immediately, allowing your orchestrator to restart it.

```python
# my_worker.py

import runpod
import torch

# Register fitness checks using the decorator
@runpod.serverless.register_fitness_check
def check_gpu_available():
    """Verify GPU is available."""
    if not torch.cuda.is_available():
        raise RuntimeError("GPU not available")

@runpod.serverless.register_fitness_check
def check_disk_space():
    """Verify sufficient disk space."""
    import shutil
    stat = shutil.disk_usage("/")
    free_gb = stat.free / (1024**3)
    if free_gb < 10:
        raise RuntimeError(f"Insufficient disk space: {free_gb:.2f}GB free")

def handler(job):
    job_input = job["input"]
    # Your handler code here
    return {"output": "success"}

# Fitness checks run before handler initialization (production only)
runpod.serverless.start({"handler": handler})
```

**Key Features:**
- Supports both synchronous and asynchronous check functions
- Hardware checks run at the first Serverless `import runpod`; processes it launches inherit the result via `RUNPOD_EARLY_FITNESS_CHECKS_DONE` and skip the pass
- Local tests and non-worker imports remain exempt; network readiness and custom checks run at worker start
- Successful early checks are reused at worker start unless their configuration changes
- Runs before handler initialization and job processing begins
- Any check failure exits with code 1 (worker marked unhealthy)

See [Worker Fitness Checks](https://github.com/runpod/runpod-python/blob/main/docs/serverless/worker_fitness_checks.md) documentation for more examples and best practices.

### Network-Volume Warm Cache

When a network volume is attached, `VolumeCache` warms local directories (such as a model cache) across cold starts — hydrating them on startup and syncing new files back on exit — so a repeated multi-GB model download becomes a one-time cost per endpoint. It is stdlib-only and best-effort.

```python
from runpod.serverless import VolumeCache

with VolumeCache(dirs=["/root/.cache/huggingface"]):
    model = load_model()
```

See [Network-Volume Warm Cache](https://github.com/runpod/runpod-python/blob/main/docs/serverless/volume_cache.md) documentation for configuration and details.

## 📚 | REST API v2 Wrapper

Use the API wrapper to manage Runpod resources through REST API v2.

```python
import runpod

runpod.api_key = "your_runpod_api_key_found_under_settings"
```

### Endpoints

You can interact with Runpod endpoints via a `run` or `run_sync` method.

#### Basic Usage

```python
endpoint = runpod.Endpoint("ENDPOINT_ID")

run_request = endpoint.run(
    {"your_model_input_key": "your_model_input_value"}
)

# Check the status of the endpoint run request
print(run_request.status())

# Get the output of the endpoint run request, blocking until the endpoint run is complete.
print(run_request.output())
```

```python
endpoint = runpod.Endpoint("ENDPOINT_ID")

run_request = endpoint.run_sync(
    {"your_model_input_key": "your_model_input_value"}
)

# Returns the job results if completed within 90 seconds, otherwise, returns the job status.
print(run_request )
```

#### API Key Management

The SDK supports multiple ways to set API keys:

**1. Global API Key** (Default)
```python
import runpod

# Set global API key
runpod.api_key = "your_runpod_api_key"

# All endpoints will use this key by default
endpoint = runpod.Endpoint("ENDPOINT_ID")
result = endpoint.run_sync({"input": "data"})
```

**2. Endpoint-Specific API Key**
```python
# Create endpoint with its own API key
endpoint = runpod.Endpoint("ENDPOINT_ID", api_key="specific_api_key")

# This endpoint will always use the provided API key
result = endpoint.run_sync({"input": "data"})
```

#### API Key Precedence

The SDK uses this precedence order (highest to lowest):
1. Endpoint instance API key (if provided to `Endpoint()`)
2. Global API key (set via `runpod.api_key`)

```python
import runpod

# Example showing precedence
runpod.api_key = "GLOBAL_KEY"

# This endpoint uses GLOBAL_KEY
endpoint1 = runpod.Endpoint("ENDPOINT_ID")

# This endpoint uses ENDPOINT_KEY (overrides global)
endpoint2 = runpod.Endpoint("ENDPOINT_ID", api_key="ENDPOINT_KEY")

# All requests from endpoint2 will use ENDPOINT_KEY
result = endpoint2.run_sync({"input": "data"})
```

#### Thread-Safe Operations

Each `Endpoint` instance maintains its own API key, making concurrent operations safe:

```python
import threading
import runpod

def process_request(api_key, endpoint_id, input_data):
    # Each thread gets its own Endpoint instance
    endpoint = runpod.Endpoint(endpoint_id, api_key=api_key)
    return endpoint.run_sync(input_data)

# Safe concurrent usage with different API keys
threads = []
for customer in customers:
    t = threading.Thread(
        target=process_request,
        args=(customer["api_key"], customer["endpoint_id"], customer["input"])
    )
    threads.append(t)
    t.start()
```

### GPU Cloud (Pods)

```python
import runpod

runpod.api_key = "your_runpod_api_key_found_under_settings"

# get all my pods
pods = runpod.get_pods()

# get a specific pod
pod = runpod.get_pod(pods[0]["id"])

# create a pod with a gpu
pod = runpod.create_pod("test", "runpod/stack", "NVIDIA GeForce RTX 4090")

# create a pod with a cpu
pod = runpod.create_pod("test", "runpod/stack", instance_id="cpu3c-2-4")

# stop the pod
runpod.stop_pod(pod["id"])

# resume the pod
runpod.resume_pod(pod["id"])

# terminate the pod
runpod.terminate_pod(pod["id"])
```

### Template and placement options

- With `create_pod(template_id=...)`, omitting `docker_args` inherits the template's
  command. Pass `docker_args=""` to clear that inherited command.
- Omitting `volume_mount_path` preserves an inherited GPU template volume's path.
  Setting it explicitly changes the path while retaining the inherited volume size.
  New persistent volumes and network volume mounts default to `/runpod-volume`.
  CPU pods do not inherit template persistent volumes; a CPU `volume_mount_path`
  requires `network_volume_id`.
- For GPU pods, `min_memory_in_gb` and `min_vcpu_count` specify minimum host RAM
  and vCPUs **per GPU**, not GPU VRAM or totals for the pod.
- CPU `instance_id` must use `<cpu-flavor>-<vcpu-count>-<memory>`, with positive
  integer vCPU and memory values, for example `cpu3c-4-8`.
- REST v2 cannot require a public-IP-capable host. `support_public_ip=False` is
  the default and does not disable public networking. `True` raises `ValueError`
  rather than silently ignoring that requirement.
- `create_template(volume_in_gb=0)` creates a template without a persistent volume.
- `create_endpoint(locations="US-KS-2,EU-RO-1")` accepts comma-separated
  datacenter IDs. Country codes such as `US` and `RO` are not supported.
  Endpoint creation sends a single REST request without catalog lookups.
- Endpoint `gpu_ids` accepts pool IDs and excluded GPU types, for example
  `gpu_ids="ADA_48_PRO,-NVIDIA L40"` selects that pool without NVIDIA L40 GPUs.
- `create_endpoint` accepts only `QUEUE_DELAY` and `REQUEST_COUNT` scaling.
  `idle_timeout` applies to `QUEUE_DELAY` (default: 5 seconds); explicitly setting
  it with `REQUEST_COUNT` raises `ValueError`.
- `resume_pod(pod_id)` keeps the existing GPU allocation. REST v2 does not support
  resizing on resume, so providing `gpu_count` raises `ValueError`.

## 📁 | Directory

```BASH
.
├── docs               # Documentation
├── examples           # Examples
├── runpod             # Package source code
│   ├── api            # rest api v2 wrapper
│   ├── cli            # Command Line Interface Functions
│   ├── endpoint       # Language library - Endpoints
│   └── serverless     # SDK - Serverless Worker
└── tests              # Package tests
```

<div align="center">

<a target="_blank" href="https://discord.gg/pJ3P2DbUUq">![Discord](https://discordapp.com/api/guilds/912829806415085598/widget.png?style=banner2)</a>

</div>
