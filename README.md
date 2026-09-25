<div align="center">
<h1>Runpod | Python Library </h1>

[![PyPI Package](https://badge.fury.io/py/runpod.svg)](https://badge.fury.io/py/runpod)
&nbsp;
[![Downloads](https://static.pepy.tech/personalized-badge/runpod?period=total&units=international_system&left_color=grey&right_color=blue&left_text=Downloads)](https://pepy.tech/project/runpod)

[![CI | End-to-End Runpod Python Tests](https://github.com/runpod/runpod-python/actions/workflows/CI-e2e.yml/badge.svg)](https://github.com/runpod/runpod-python/actions/workflows/CI-e2e.yml)

[![CI | Unit Tests](https://github.com/runpod/runpod-python/actions/workflows/CI-pytests.yml/badge.svg)](https://github.com/runpod/runpod-python/actions/workflows/CI-pytests.yml)
&nbsp;
[![CI | CodeQL](https://github.com/runpod/runpod-python/actions/workflows/CI-codeql.yml/badge.svg)](https://github.com/runpod/runpod-python/actions/workflows/CI-codeql.yml)

</div>

Welcome to the official Python library for Runpod API &amp; SDK.

## Table of Contents

- [Table of Contents](#table-of-contents)
- [💻 | Installation](#--installation)
- [⚡ | Serverless Worker (SDK)](#--serverless-worker-sdk)
  - [Quick Start](#quick-start)
  - [Local Test Worker](#local-test-worker)
- [📚 | REST API v2 Wrapper](#--rest-api-v2-wrapper)
  - [Endpoints](#endpoints)
  - [GPU Cloud (Pods)](#gpu-cloud-pods)
  - [Logs](#logs)
- [📁 | Directory](#--directory)
- [🤝 | Community and Contributing](#--community-and-contributing)

## 💻 | Installation

### Install from PyPI (Stable Release)

```bash
# Install with pip
pip install runpod

# Install with uv (faster alternative)
uv add runpod
```

### Install from GitHub (Latest Changes)

To get the latest changes that haven't been released to PyPI yet:

```bash
# Install latest development version from main branch with pip
pip install git+https://github.com/runpod/runpod-python.git

# Install with uv
uv add git+https://github.com/runpod/runpod-python.git

# Install a specific branch
pip install git+https://github.com/runpod/runpod-python.git@branch-name

# Install a specific tag/release
pip install git+https://github.com/runpod/runpod-python.git@v1.0.0

# Install in editable mode for development
git clone https://github.com/runpod/runpod-python.git
cd runpod-python
pip install -e .
```

*Python 3.10 or higher is required to use the latest version of this package.*

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

### Logs

Pod and Serverless worker logs are read from the REST v2 log streams. Each entry
is a dict with `id`, `ts`, `source` (`container` or `system`) and `line`.

```python
import runpod

# snapshot: backfill the last 200 container lines, then read live output for 5s
logs = runpod.get_pod_logs(pod["id"], tail=200, source="container", max_wait=5)
print("\n".join(entry["line"] for entry in logs))

# follow: yield lines as they arrive until you stop iterating
for entry in runpod.iter_pod_logs(pod["id"], tail=0):
    print(entry["ts"], entry["line"])

# Serverless workers
workers = runpod.get_endpoint_workers("ENDPOINT_ID")
logs = runpod.get_endpoint_worker_logs("ENDPOINT_ID", workers[0]["id"])
```

- `tail` backfills 0–5000 historical lines (API default 100) and is ignored when
  `since` is set. `since` takes an RFC3339 string or a timezone-aware `datetime`.
- `get_*_logs` returns once `max_wait` seconds pass, or once the stream has been
  idle that long. Past `max_bytes` of log text (default 4 MiB) the oldest lines
  are dropped, so the newest output is always kept.
- `iter_*_logs` reconnects from the last event ID when the stream closes or goes
  idle, so lines are neither repeated nor skipped. It waits out `429` responses
  on reconnect. Errors on the first connection raise. Pass `max_wait` to stop
  after that many seconds.

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

## 🤝 | Community and Contributing

We welcome both pull requests and issues on [GitHub](https://github.com/runpod/runpod-python). Bug fixes and new features are encouraged, but please read our [contributing guide](CONTRIBUTING.md) first.

<div align="center">

<a target="_blank" href="https://discord.gg/pJ3P2DbUUq">![Discord Banner 2](https://discordapp.com/api/guilds/912829806415085598/widget.png?style=banner2)</a>

</div>
