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
```

## Overview

```bash
rp --help
```

### Authenticate

```bash
rp login                        # browser approval
rp login --api-key YOUR_KEY     # store a key directly
```

Credentials are stored in `~/.runpod/config.toml`.

### Flash apps

```bash
rp flash init my-app              # scaffold a project
rp flash dev main.py              # run a live development session
rp flash deploy                   # deploy the current project
rp flash app list                 # list deployed apps
rp flash env list --app my-app    # list an app's environments
rp flash undeploy --app my-app    # delete an environment's endpoints
```
