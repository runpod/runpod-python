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
Each handle permits one active context; overlapping entry is rejected.

Failed creation triggers bounded cleanup. `SandboxCreationError`, exported from
`runpod.sandbox`, preserves the backend's `sandbox_id`, HTTP status, and error
details. If termination also fails, the cleanup error is chained as `__cause__`;
the ID remains available for explicit recovery with `get(...)` and `terminate()`.

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

`exec(..., timeout_seconds=30)` requests a command timeout from 1 to 50 seconds.
The backend currently caps foreground execution at about four seconds.
`request_timeout` separately limits the client HTTP wait.
For longer work, use `background=True` and poll for completion. A successful
detached result confirms that the command started, not that it finished; redirect
output to a file to retrieve it afterward. These options require backend support
for `timeoutSeconds` and `background`.

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
