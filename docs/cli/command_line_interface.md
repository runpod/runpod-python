# RunPod CLI

Note: This CLI is not the same as runpodctl and provides a different set of features.

```bash
# Auth
runpod config

runpod ssh list-keys
runpod ssh add-key

runpod list pods
runpod create pod
runpod connect pod

runpod exec python file.py
```

## Overview

```bash
runpod --help
```

### Configure

```bash
$ runpod config
Profile [default]:
RunPod API Key [None]: YOUR_RUNPOD_API_KEY
```

### Launch Pod

```bash
runpod launch --help
runpod launch pod --template-file template.yaml
```

### Launch Endpoint

```bash
runpod launch endpoint --help
runpod launch endpoint --template-file template.yaml
```
