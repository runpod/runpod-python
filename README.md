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

Create an executable file called 'worker' in the root of your project that contains the following:

```python
#!/usr/bin/env python

import runpod

runpod.serverless.pod_worker.start_worker()
```

Add the env variables found in [serverless-worker](docs/serverless-worker.md) to your project.
