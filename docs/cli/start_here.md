# Runpod CLI Reference

Note: This CLI is not the same as runpodctl and provides a different set of features.

## Getting Started

![rp --help](demos/help.gif)

### Authenticate

Store your Runpod API key by running `rp login` (approve in the browser, then paste the key the console shows) or `rp login --api-key YOUR_KEY`. Credentials are stored in `~/.runpod/config.toml`.

Pass `--profile staging` with either login method to update a named credential profile without changing the others. The default profile is `default`.

Set `RUNPOD_PROFILE=staging` to use that profile for every command, including `rp login`. `--profile` takes precedence over `RUNPOD_PROFILE`, and `RUNPOD_API_KEY` takes precedence over any stored profile.

### SSH

Add an SSH key to your account by running `rp ssh add`. To use an existing key pass `--key` or `--key-file`. Keys are stored in `~/.runpod/ssh/`. If no key is specified a new one is generated and stored.

Once a key is added, open a terminal on any pod with `rp ssh <pod_id>` (or the equivalent `rp pod connect <pod_id>`).

## Apps

An app is a collection of Python functions that run on Runpod. Scaffold one with `rp flash init`, iterate on it live with `rp flash dev main.py`, and ship it with `rp flash deploy`. See the [README](../../README.md) for the full workflow.

### Delete an app and clean up storage

`rp flash app delete my-app` deletes the app's endpoints and environments.
Network and global volumes are retained and continue billing. The command
warns about this even with `--yes`. Check whether another app or pod uses a
volume before deleting it. Volume deletion permanently deletes its data.

```bash
rp volume list
rp volume list --global
rp volume delete VOLUME_ID
rp volume delete GLOBAL_VOLUME_ID --global
```

Volume deletion asks for confirmation. Pass `--yes` to confirm deletion in
scripts. The async SDK accepts volume IDs and raises API errors, including
errors for volumes that are still in use:

```python
import asyncio
from runpod.apps.api import AppsApiClient

async def cleanup():
    client = AppsApiClient()
    await client.delete_network_volume("NETWORK_VOLUME_ID")
    await client.delete_global_volume("GLOBAL_VOLUME_ID")

asyncio.run(cleanup())
```
