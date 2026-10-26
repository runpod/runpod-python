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
rp network-volume upload VOLUME_ID ./dataset datasets/dataset
rp network-volume download VOLUME_ID outputs/ ./outputs
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
rp network-volume upload VOLUME_ID ./model.safetensors models/
rp network-volume upload VOLUME_ID ./dataset datasets/dataset
rp network-volume download VOLUME_ID models/model.safetensors
rp network-volume download VOLUME_ID outputs/ ./outputs
```

Transfers use the volume's S3-compatible API, so they need S3 API keys
(separate from the API key): set `RUNPOD_S3_ACCESS_KEY` and
`RUNPOD_S3_SECRET_KEY`, or pass `--s3-access-key` / `--s3-secret-key`. The
volume must be in a [datacenter with the S3 API](https://docs.runpod.io/storage/s3-api).
Its datacenter is looked up with your API key unless you pass `--datacenter`.

To resume an interrupted transfer, run the same command again. Files that
are unchanged (same size, destination not older) are skipped, a large upload
reuses the parts it already sent, and a large download keeps its finished
chunks in `<name>.rpdownload`. `--force` transfers every file. Parts of an
upload that is never resumed stay in the volume's hidden `.s3compat_uploads`
folder and count toward its size.

The same transfers are available from Python as
`runpod.upload_to_network_volume` and `runpod.download_from_network_volume`.
