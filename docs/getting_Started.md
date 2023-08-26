# Getting Started

The runpod python library is a powerful library providing SDK functions, API access, and CLI commands for interacting with the runpod platform.

## Credentials File

This python library supports a credentials file saved to `~/.runpod/credentials.toml` that contains the following information:

```toml
[profile]
api_key = "YOUR_RUNPOD_API_KEY"
```
### Profile

By default all credentials are stored under the `default` profile. To switch profiles you can use the `--profile` argument on CLI commands or change the profile within a script.

```python
import runpod

runpod.profile = "my_profile"
```
