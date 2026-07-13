# Runpod CLI

Note: This CLI is not the same as runpodctl and provides a different set of features.

```bash
# Auth
rp login

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
