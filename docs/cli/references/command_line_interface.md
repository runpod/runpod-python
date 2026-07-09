# Runpod CLI

Note: This CLI is not the same as runpodctl and provides a different set of features.

```bash
# Auth
rp login

# SSH keys (used by rp pod connect)
rp ssh list
rp ssh add

# Pods
rp pod list
rp pod create
rp pod connect
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
