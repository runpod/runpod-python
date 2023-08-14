<div align="center">
<h1>RunPod | Python Library </h1>

[![PyPI Package](https://badge.fury.io/py/runpod.svg)](https://badge.fury.io/py/runpod)
&nbsp;
[![Downloads](https://static.pepy.tech/personalized-badge/runpod?period=total&units=international_system&left_color=grey&right_color=blue&left_text=Downloads)](https://pepy.tech/project/runpod)

[![CI | End-to-End RunPod Python Tests](https://github.com/runpod/runpod-python/actions/workflows/CI-e2e.yml/badge.svg)](https://github.com/runpod/runpod-python/actions/workflows/CI-e2e.yml)

[![CI | Code Quality](https://github.com/runpod/runpod-python/actions/workflows/CI-pylint.yml/badge.svg)](https://github.com/runpod/runpod-python/actions/workflows/CI-pylint.yml)
&nbsp;
[![CI | Unit Tests](https://github.com/runpod/runpod-python/actions/workflows/CI-tests.yml/badge.svg)](https://github.com/runpod/runpod-python/actions/workflows/CI-tests.yml)
&nbsp;
[![CI | CodeQL](https://github.com/runpod/runpod-python/actions/workflows/CI-codeql.yml/badge.svg)](https://github.com/runpod/runpod-python/actions/workflows/CI-codeql.yml)

</div>

Welcome to the official Python library for RunPod API &amp; SDK.

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

```bash
# Install the latest release version
pip install runpod

# or

# Install the latest development version (main branch)
pip install git+https://github.com/runpod/runpod-python.git
```

*Python 3.10 or higher is required to use the latest version of this package.*

## ⚡ | Serverless Worker (SDK)

This python package can also be used to create a serverless worker that can be deployed to RunPod as a custom endpoint API.

### Quick Start

Create an python script in your project that contains your model definition and the RunPod worker start code. Run this python code as your default container start command:

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

Make sure that this file is ran when your container starts. This can be accomplished by calling it in the docker command when you setup a template at [runpod.io/console/serverless/user/templates](https://www.runpod.io/console/serverless/user/templates) or by setting it as the default command in your Dockerfile.

See our [blog post](https://www.runpod.io/blog/serverless-create-a-basic-api) for creating a basic Serverless API, or view the [details docs](https://docs.runpod.io/serverless-ai/custom-apis) for more information.

### Local Test Worker

You can also test your worker locally before deploying it to RunPod. This is useful for debugging and testing.

```bash
python my_worker.py --rp_serve_api
```

## 📚 | API Language Library (GraphQL Wrapper)

When interacting with the RunPod API you can use this library to make requests to the API.

```python
import runpod

runpod.api_key = "your_runpod_api_key_found_under_settings"
```

### Endpoints

You can interact with RunPod endpoints via a `run` or `run_sync` method.

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

### GPU Cloud (Pods)

```python
import runpod

runpod.api_key = "your_runpod_api_key_found_under_settings"

# Create a pod
pod = runpod.create_pod("test", "runpod/stack", "NVIDIA GeForce RTX 3070")

# Stop the pod
runpod.stop_pod(pod.id)

# Start the pod
runpod.start_pod(pod.id)

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

We welcome both pull requests and issues on [GitHub](https://github.com/runpod/runpod-python). Bug fixes and new features are encouraged.

<div align="center">

<a target="_blank" href="https://discord.gg/pJ3P2DbUUq">![Discord Banner 2](https://discordapp.com/api/guilds/912829806415085598/widget.png?style=banner2)</a>

</div>
