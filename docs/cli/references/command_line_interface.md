# Runpod CLI

Note: This CLI is not the same as runpodctl and provides a different set of features.

```bash
# Auth
rp login

# Flash apps
rp flash init my-app
rp flash dev main.py
rp flash deploy
rp flash app list
rp flash env list --app my-app
rp flash undeploy --app my-app

# SSH
rp ssh add          # add a key to your account
rp ssh list         # list account keys
rp ssh POD_ID       # open a terminal on a pod

# Pods
rp pod list
rp pod create
rp pod connect POD_ID

# Network volume files
rp volume upload VOLUME ./dataset datasets/dataset
rp volume download VOLUME outputs/ ./outputs
```

## Overview

```bash
rp --help
```

### Authenticate

```bash
rp login                        # browser approval
rp login --api-key YOUR_KEY     # store a key directly
rp login --profile staging      # save browser credentials in a named profile
```

Credentials are stored in `~/.runpod/config.toml`.
Use `--profile NAME` with either login method to update only that profile; the default is `default`.

### Flash apps

```bash
rp flash init my-app              # scaffold a project
rp flash dev main.py              # run a live development session
rp flash deploy                   # deploy the current project
rp flash app list                 # list deployed apps
rp flash env list --app my-app    # list an app's environments
rp flash undeploy --app my-app    # delete an environment's endpoints
```

App discovery imports modules synchronously. Keep blocking work inside an
entrypoint or a resource function; interrupt discovery if an import blocks.

On Ctrl-C, a dev session waits for its running local entrypoint to finish before
deleting endpoints. Blocking entrypoint code can delay shutdown. Cleanup failures
exit with an error and report the endpoint IDs that still need deletion.

### Network volume files

```bash
rp volume upload VOLUME ./model.safetensors models/
rp volume upload VOLUME ./dataset datasets/dataset
rp volume download VOLUME models/model.safetensors
rp volume download VOLUME outputs/ ./outputs
```

Transfers use the volume's S3-compatible API, so they need S3 API keys
(separate from the API key): set `RUNPOD_S3_ACCESS_KEY` and
`RUNPOD_S3_SECRET_KEY`, pass `--s3-access-key` / `--s3-secret-key`, or save
`s3_access_key` / `s3_secret_key` in `~/.runpod/config.toml`. The CLI prompts
for missing keys and offers to save them. You can omit `VOLUME` in an
interactive session to pick a volume from a list. The
volume must be in a [datacenter with the S3 API](https://docs.runpod.io/storage/s3-api).
`VOLUME` is the volume's ID or name, looked up with your API key.

To resume an interrupted transfer, run the same command again. Files that
are unchanged (same size, destination not older) are skipped, a large upload
reuses the parts it already sent, and a large download keeps its finished
chunks in `<name>.rpdownload`. `--force` transfers every file. Parts of an
upload that is never resumed stay in the volume's hidden `.s3compat_uploads`
folder and count toward its size.

The same transfers are available from Python:

```python
from runpod import NetworkVolume

volume = NetworkVolume("my-volume")
volume.upload("./dataset", "datasets/dataset")
volume.download("outputs/", "./outputs")
```
