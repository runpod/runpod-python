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


<div align="center">

<a target="_blank" href="https://discord.gg/pJ3P2DbUUq">![Discord](https://discordapp.com/api/guilds/912829806415085598/widget.png?style=banner2)</a>

</div>
