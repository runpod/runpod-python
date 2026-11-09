<div align="center">

<h1>Runpod Python</h1>

**Define GPU functions in Python. Run them in the cloud with one decorator.**

[![PyPI Package](https://badge.fury.io/py/runpod.svg)](https://badge.fury.io/py/runpod)
[![Downloads](https://static.pepy.tech/personalized-badge/runpod?period=total&units=international_system&left_color=grey&right_color=blue&left_text=downloads)](https://pepy.tech/project/runpod)
[![CI | Unit Tests](https://github.com/runpod/runpod-python/actions/workflows/CI-pytests.yml/badge.svg)](https://github.com/runpod/runpod-python/actions/workflows/CI-pytests.yml)
[![License](https://img.shields.io/badge/License-MIT-blue)](LICENSE)

[Documentation](https://docs.runpod.io) • [Examples](examples) • [Discord](https://discord.gg/pJ3P2DbUUq)

</div>

<!-- runpod-agent-setup:start -->
**Using an AI agent?** Point it to https://docs.runpod.io/agent-setup.md to install the Runpod skills and MCP server.
<!-- runpod-agent-setup:end -->

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

checkpoints = NetworkVolume("checkpoints", size=100)
llama = Model("meta-llama/Llama-3.1-8B-Instruct")


# an autoscaling job queue on cloud H100s: weights pre-cached,
# dependencies vendored at deploy time, scale-to-zero when idle
@app.queue(
    gpu="H100",
    workers=(0, 3),
    dependencies=["vllm"],
    model=llama,
    env={"HF_TOKEN": Secret("hf-token")},
)
def chat(prompt: str):
    import vllm

    llm = vllm.LLM(model=str(llama.path))   # weights already on disk
    result = llm.generate([prompt])[0]
    return result.outputs[0].text           # results must be JSON values


# one ephemeral pod per call: provisions, runs to completion, terminates
@app.task(gpu="H100", mounts={"/checkpoints": checkpoints})
def finetune(steps: int = 1000):
    if steps < 1:
        raise ValueError("steps must be a positive integer")

    import torch

    model = torch.nn.Linear(1, 1).cuda()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.05)
    x = torch.randn(1024, 1, device="cuda")
    y = 3 * x + 1
    for _ in range(steps):
        loss = ((model(x) - y) ** 2).mean()
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
    torch.save(model.state_dict(), checkpoints.path / "model.pt")
    return {"loss": loss.item()}


@runpod.local_entrypoint
def main():
    print(chat.remote("why is the sky blue?"))   # blocks for the result
    print(finetune.remote(steps=500))            # pod terminates on return
```

```bash
rp secret add hf-token  # store your Hugging Face token once
rp flash dev main.py    # live dev session: edit, re-run, logs stream back
rp flash deploy         # deploy production endpoints
```

## Contributing

Pull requests and issues are welcome — see the [contributing guide](CONTRIBUTING.md) to get started.

```bash
git clone https://github.com/runpod/runpod-python.git
cd runpod-python
make setup
make test
```

## Remote values

Queue and task calls use finite JSON-compatible arguments, return values,
and stream chunks. JSON conversion turns tuples into lists and supported
dictionary keys into strings, including in same-worker remote calls.
Bytes, custom objects, circular references, and non-finite numbers produce
explicit errors. `.local()` remains a direct Python call.

Successful function responses contain `json_result`, including for a null
return value. SDK and worker runtimes share the JSON protocol validators;
use compatible package releases when building worker images.

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
