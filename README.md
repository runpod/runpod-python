<div align="center">
<h1>RunPod | Python Library </h1>

[![CI | Code Quality](https://github.com/runpod/runpod-python/actions/workflows/ci_pylint.yml/badge.svg)](https://github.com/runpod/runpod-python/actions/workflows/ci_pylint.yml)
&nbsp;
[![CI | Unit Tests](https://github.com/runpod/runpod-python/actions/workflows/CI_tests.yml/badge.svg)](https://github.com/runpod/runpod-python/actions/workflows/CI_tests.yml)

</div>

Official Python library for RunPod API &amp; SDK.

## Table of Contents

- [Table of Contents](#table-of-contents)
- [Installation](#installation)
- [API Language Library](#api-language-library)
- [SDK - Serverless Worker](#sdk---serverless-worker)
  - [Quick Start](#quick-start)

## Installation

```bash
pip install runpod
```

## API Language Library

When interacting with the RunPod API you can use this library to make requests to the API.

```python
import runpod

runpod.api_key = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

## SDK - Serverless Worker

This python package can also be used to create a serverless worker that can be deployed to RunPod.

### Quick Start

Create an python script in your project that contains your model definition and the RunPod worker start code. Run this python code as your default container start command:

```python
import runpod

MODEL = 'YOUR_MODEL'

def run(job):
    # Your inference code here
    return MODEL.predict(job.input)

runpod.serverless.start({"handler": run})
```

Add the env variables found in [serverless-worker](docs/serverless-worker.md) to your project.
