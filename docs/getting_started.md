# Getting Started

The Runpod python library is a powerful library providing SDK functions, API access, and CLI commands for interacting with the Runpod platform.

## Credentials File

`rp login` stores your API key in `~/.runpod/config.toml`, one table per profile:

```toml
[default]
api_key = "YOUR_RUNPOD_API_KEY"
```

### Profile

By default, credentials are stored under and read from the `default` profile. To store a key under another profile, pass `--profile` to `rp login`:

```bash
rp login --profile my_profile
```

To use that profile, set the `RUNPOD_PROFILE` environment variable before importing `runpod`. The library reads the profile and its API key once, at import time.

```bash
export RUNPOD_PROFILE=my_profile
```

`RUNPOD_API_KEY`, when set, takes precedence over any stored profile. Inside a script, you can also set the key directly:

```python
import runpod

runpod.api_key = "YOUR_RUNPOD_API_KEY"
```
