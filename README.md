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
from runpod import App, Model, Secret, Volume

app = App("inference")

models = Volume("models", size=100)
llama = Model("meta-llama/Llama-3.1-8B-Instruct")


# an autoscaling job queue on cloud H100s: weights pre-cached,
# dependencies vendored at deploy time, scale-to-zero when idle
@app.queue(
    gpu="H100",
    workers=(0, 3),
    dependencies=["vllm"],
    volume=models,
    model=llama,
    env={"HF_TOKEN": Secret("hf-token")},
)
def chat(prompt: str):
    import vllm

    llm = vllm.LLM(model=str(llama.path))   # weights already on disk
    return llm.generate(prompt)


# one ephemeral pod per call: provisions, runs to completion, terminates
@app.task(gpu="H100", gpu_count=2, volume=models)
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

## Contributing

Pull requests and issues are welcome — see the [contributing guide](CONTRIBUTING.md) to get started.

```bash
git clone https://github.com/runpod/runpod-python.git
cd runpod-python
make setup
make test
```

## REST API v2

Manage pods, endpoints, and templates through the API helpers. See the
[REST wrapper example](examples/rest_wrapper.py) and [API guide](docs/api/queries.md).

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
<div align="center">

<a target="_blank" href="https://discord.gg/pJ3P2DbUUq">![Discord](https://discordapp.com/api/guilds/912829806415085598/widget.png?style=banner2)</a>

</div>
