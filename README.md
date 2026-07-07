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

The official Python SDK for [Runpod](https://runpod.io): define GPU functions in Python, run them in the cloud with one decorator, and deploy them as production endpoints.

```python
import runpod
from runpod import App

app = App("my-app")

@app.queue(gpu="4090")
def embed(text: str):
    import torch
    ...
    return vector

@runpod.local_entrypoint
def main():
    print(embed.remote("hello world"))   # runs on a cloud 4090
```

```bash
pip install runpod
rp login          # browser auth
rp dev main.py    # live dev session: edit, re-run, logs stream back
rp deploy         # production endpoints
```

## Table of Contents

- [Installation](#--installation)
- [Apps: Python Functions in the Cloud](#--apps-python-functions-in-the-cloud)
  - [Resources: queue, api, task](#resources-queue-api-task)
  - [Calling Functions](#calling-functions)
  - [Hardware Selection](#hardware-selection)
  - [Dependencies and Images](#dependencies-and-images)
  - [Volumes](#volumes)
  - [Secrets](#secrets)
  - [Cached Models](#cached-models)
  - [Private Registries](#private-registries)
  - [The Dev Loop](#the-dev-loop)
  - [Deploying](#deploying)
  - [CLI Reference](#cli-reference)
- [Serverless Worker (SDK)](#--serverless-worker-sdk)
- [API Language Library](#--api-language-library-graphql-wrapper)
- [Directory](#--directory)
- [Community and Contributing](#--community-and-contributing)

## 💻 | Installation

```bash
# stable
pip install runpod

# or with uv
uv add runpod

# latest development version
pip install git+https://github.com/runpod/runpod-python.git
```

*Python 3.10 or higher is required.*

Installing the package also installs the `rp` CLI (also available as `runpod`). Authenticate once:

```bash
rp login
```

## 🚀 | Apps: Python Functions in the Cloud

An **App** is a named collection of functions that run on Runpod. Decorate plain Python functions; the SDK handles packaging, provisioning, scaling, and routing.

### Resources: queue, api, task

Three decorators cover the three execution models:

```python
import runpod
from runpod import App

app = App("inference")

# queue: autoscaling job queue (scale-to-zero serverless workers)
@app.queue(gpu="4090")
def transcribe(audio_url: str):
    ...
    return text

# api: load-balanced http service from a class
from runpod import init, get, post

@app.api(cpu="cpu5c-2-4")
class Counter:
    @init
    def setup(self):                 # runs once per worker, before traffic
        self.count = 0

    @post("/bump")
    async def bump(self, body: dict):
        self.count += body.get("by", 1)
        return {"count": self.count}

    @get("/value")
    async def value(self):
        return {"count": self.count}

# task: one ephemeral pod per call, runs to completion, then terminates
@app.task(gpu="H100", gpu_count=2)
def train(steps: int = 1000):
    ...
    return {"loss": final_loss}
```

Pick by shape of work:

| | `@app.queue` | `@app.api` | `@app.task` |
|---|---|---|---|
| execution | job queue | http routes | dedicated pod per call |
| scaling | 0..N workers | 0..N workers | one pod, then gone |
| best for | inference jobs | rest services, streaming | training runs, batch jobs |
| duration | seconds to minutes | request-scoped | minutes to hours |

`@app.api` also accepts a zero-argument function returning an ASGI app (FastAPI, Starlette) if you'd rather bring your own framework.

### Calling Functions

Every decorated function keeps its normal Python identity and gains remote invocation:

```python
transcribe.remote(url)              # run remotely, block for the result
await transcribe.remote.aio(url)    # async variant
transcribe.spawn(url)               # fire and forget -> Job
transcribe.local(url)               # run in this process (no cloud)

job = transcribe.spawn(url)
result = job.result()               # collect later
```

Functions can call each other — from your laptop, or from inside another worker:

```python
@app.queue(cpu="cpu3c-1-2")
def orchestrate(x: int):
    return {"embedding": embed.remote(x), "meta": lookup.remote(x)}
```

`@runpod.local_entrypoint` marks the function `rp dev` runs as your client-side script.

### Hardware Selection

GPUs resolve from pool ids, exact device names, or shorthand fragments:

```python
@app.queue(gpu="4090")                        # NVIDIA GeForce RTX 4090
@app.queue(gpu="H100")                        # all H100 variants
@app.queue(gpu="B200", gpu_count=8)           # multi-gpu
@app.queue(gpu=GpuGroup.ADA_24)               # pool id enum
@app.queue(gpu=["4090", "5090"])              # any of several
@app.queue(cpu="cpu5c-2-4")                   # cpu instance flavor
```

Unknown names fail at import time with the full list of valid options — not at provision time with a cryptic API error.

### Dependencies and Images

```python
@app.queue(
    cpu="cpu3c-1-2",
    dependencies=["pyfiglet", "numpy>=2"],    # pip packages
    system_dependencies=["ffmpeg"],           # apt packages
)
def render(text: str): ...
```

At deploy time the SDK resolves your dependency closure into the build artifact, so cold starts never run pip. Prefer a fully custom image:

```python
@app.queue(image="ghcr.io/me/mine:latest", registry_auth="my-ghcr")
def custom(): ...
```

Custom images must include `python3`; the worker runtime bootstraps itself.

### Volumes

Network volumes persist across calls and are shared between resources:

```python
from runpod import Volume

models = Volume("models", size=100)     # created on first use

@app.task(gpu="H100", volume=models)
def train():
    torch.save(state, models.path / "run-1/model.pt")

@app.queue(gpu="4090", volume=models)
def infer(prompt: str):
    sd = torch.load(models.path / "run-1/model.pt")
```

A volume lives in one datacenter, and everything attached to it must schedule there. The SDK solves placement over the whole app: it intersects each resource's hardware availability per datacenter, picks the one where the most-constrained resource has the best stock, and errors with a per-resource breakdown when no datacenter can host everyone.

`volume.path` resolves to the platform mount inside the worker (`/workspace` on task pods, `/runpod-volume` on endpoint workers). Endpoints may take a list of volumes (one per datacenter) to serve from multiple regions.

### Secrets

Platform-encrypted values, decrypted only inside the worker:

```bash
rp secret add hf-token        # prompts for the value
```

```python
from runpod import Secret

@app.queue(gpu="4090", env={"HF_TOKEN": Secret("hf-token")})
def download(): ...           # worker sees the real value in $HF_TOKEN
```

The value never passes through the SDK. Deploys fail fast when a referenced secret does not exist.

### Cached Models

Reference HuggingFace weights and the platform stages them on the host **before** your worker starts — cold starts skip the download entirely:

```python
from runpod import Model

llama = Model("meta-llama/Llama-3.1-8B-Instruct")

@app.queue(gpu="H100", model=llama, env={"HF_TOKEN": Secret("hf-token")})
def chat(prompt: str):
    llm = vllm.LLM(model=str(llama.path))    # weights already on disk
```

Weights also appear in the standard HuggingFace cache layout, so `transformers` and `vllm` find them with zero configuration. Gated repos authenticate with an `HF_TOKEN` env var (a `Secret` works).

### Private Registries

```bash
rp registry add my-ghcr       # prompts for username/password
```

```python
@app.queue(image="ghcr.io/me/private:latest", registry_auth="my-ghcr")
def infer(): ...
```

### The Dev Loop

```bash
rp dev main.py
```

provisions temporary live endpoints for your app, runs your `@runpod.local_entrypoint`, and streams everything back:

```
dev inference · main.py
  transcribe  queue  4090  se3u174ensbubc ↗

 ▸ main() local entrypoint
 ○ transcribe()
 ● transcribe() running on worker 1x74v8dg5e9i
   transcribe │ loading model...
   transcribe │ transcribing 42s of audio
 ✓ transcribe() in 12.4s

 ✓ done in 12.6s · enter re-run · edit reload · ^C quit
```

- **edit a file** → session reloads, shows a diff of what changed, next call runs the new code
- **enter** → re-run the entrypoint
- **worker stdout streams live** into your terminal, attributed per function
- **ctrl-c** → everything is cleaned up; dev sessions leave nothing behind

Code ships with each request during dev, so there is no build step between edits.

### Deploying

```bash
rp deploy               # every app found in the current directory
rp deploy main.py       # one file
rp deploy -e staging    # a named environment
```

Deploys package your project and its resolved dependency environment into a build artifact, upload it once, and reconcile one endpoint per queue/api resource (tasks provision per call). Workers cold-start from the artifact — no dependency resolution at runtime.

```
deploy inference → default · transcribe
✓ resolving dependencies 89 packages
✓ packaging artifact
✓ uploading build        ━━━━━━━━━━ 53.3 MB
✓ reconciling endpoints
✓ inference/default is live 44.9s

  transcribe  gumuj47vi1xu4y ↗
```

Manage what's deployed:

```bash
rp app list                       # all apps and environments
rp env get default -a inference   # endpoints in an environment
rp undeploy -a inference          # tear down an environment's endpoints
rp app delete inference           # remove the app entirely
```

### CLI Reference

| command | |
|---|---|
| `rp login` | browser auth (or `--api-key` to paste a key) |
| `rp dev <file>` | live dev session |
| `rp deploy [target]` | build and deploy apps |
| `rp undeploy` | tear down an environment |
| `rp app list/get/delete` | app management |
| `rp env list/get/create/delete` | environment management |
| `rp secret add/list/rm` | platform secrets |
| `rp registry add/list/rm` | registry credentials |
| `rp logs <pod_id> [-f]` | pod logs (snapshot or follow) |
| `rp pod / ssh / exec / config` | pod management |

## ⚡ | Serverless Worker (SDK)

This package is also what you use to build the worker side of a custom serverless endpoint: a handler function that processes jobs.

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

Make sure that this file is run when your container starts. This can be accomplished by calling it in the docker command when you set up a template at [console.runpod.io/serverless/user/templates](https://console.runpod.io/serverless/user/templates) or by setting it as the default command in your Dockerfile.

See our [blog post](https://www.runpod.io/blog/build-basic-serverless-api) for creating a basic Serverless API, or view the [detailed docs](https://docs.runpod.io/serverless-ai/custom-apis) for more information.

### Local Test Worker

You can test your worker locally before deploying it to Runpod:

```bash
python my_worker.py --rp_serve_api
```

### Worker Fitness Checks

Fitness checks validate your worker environment at startup before processing jobs. If any check fails, the worker exits immediately, allowing your orchestrator to restart it.

```python
# my_worker.py

import runpod
import torch

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

runpod.serverless.start({"handler": handler})
```

Built-in system fitness checks (GPU health, disk, memory, CUDA, network) run automatically on serverless workers.

## 📚 | API Language Library (GraphQL Wrapper)

When interacting with the Runpod API you can use this library to make requests.

```python
import runpod

runpod.api_key = "your_runpod_api_key_found_under_settings"
```

### Endpoints

Interact with existing endpoints by id:

```python
import runpod

endpoint = runpod.Endpoint("ENDPOINT_ID")

run_request = endpoint.run(
    {"your_model_input_key": "your_model_input_value"}
)

# Check the status of the endpoint run request
print(run_request.status())

# Get the output of the endpoint run request, blocking until complete
print(run_request.output())
```

```python
import runpod

endpoint = runpod.Endpoint("ENDPOINT_ID")

run_request = endpoint.run_sync(
    {"your_model_input_key": "your_model_input_value"}
)

# Returns the job results if completed within 90 seconds, otherwise, returns the job status.
print(run_request)
```

#### Per-Endpoint API Keys

Each `Endpoint` instance can use its own API key, useful for multi-tenant scenarios:

```python
import runpod

# Endpoint using a specific API key
endpoint1 = runpod.Endpoint("ENDPOINT_ID_1", api_key="CUSTOMER_A_KEY")
endpoint2 = runpod.Endpoint("ENDPOINT_ID_2", api_key="CUSTOMER_B_KEY")

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

# Get all my pods
pods = runpod.get_pods()

# Get a specific pod
pod = runpod.get_pod(pod.id)

# Create a pod with GPU
pod = runpod.create_pod("test", "runpod/stack", "NVIDIA GeForce RTX 3070")

# Create a pod with CPU
pod = runpod.create_pod("test", "runpod/stack", instance_id="cpu3c-2-4")

# Stop the pod
runpod.stop_pod(pod.id)

# Resume the pod
runpod.resume_pod(pod.id)

# Terminate the pod
runpod.terminate_pod(pod.id)
```

## 📁 | Directory

```BASH
.
├── docs               # Documentation
├── examples           # Examples
├── runpod             # Package source code
│   ├── api            # Language library - API (GraphQL)
│   ├── apps           # Apps SDK (App, queue/api/task, volumes, secrets)
│   ├── cli            # Legacy CLI functions
│   ├── endpoint       # Language library - Endpoints
│   ├── rp_cli         # The rp / runpod CLI
│   ├── runtimes       # Worker runtime images (queue, api, task)
│   └── serverless     # SDK - Serverless Worker
└── tests              # Package tests
```

## 🤝 | Community and Contributing

We welcome both pull requests and issues on [GitHub](https://github.com/runpod/runpod-python). Bug fixes and new features are encouraged, but please read our [contributing guide](CONTRIBUTING.md) first.

<div align="center">

<a target="_blank" href="https://discord.gg/pJ3P2DbUUq">![Discord Banner 2](https://discordapp.com/api/guilds/912829806415085598/widget.png?style=banner2)</a>

</div>
