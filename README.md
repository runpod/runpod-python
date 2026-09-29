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
