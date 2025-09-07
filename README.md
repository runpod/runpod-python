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
- [📚 | API Language Library (GraphQL Wrapper)](#--api-language-library-graphql-wrapper)
  - [Endpoints](#endpoints)
  - [GPU Cloud (Pods)](#gpu-cloud-pods)
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

*Python 3.8 or higher is required to use the latest version of this package.*

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

## 📚 | API Language Library (GraphQL Wrapper)

When interacting with the Runpod API you can use this library to make requests to the API.

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
│   ├── api_wrapper    # Language library - API (GraphQL)
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
