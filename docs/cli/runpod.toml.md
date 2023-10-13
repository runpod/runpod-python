# Project File

Project files are stored as a `runpod.toml` file in the root of your project. This file contains all the information needed to run your project on the runpod platform.

## Example

```toml
[project]
uuid = "00000000"
name = "My Project"
baseimage = "runpod/base:0.0.0"
gpu = "NVIDIA RTX 3090"
gpucount = 1
storageid = "00000000"
volume_mount_path = "/runpod-volume"
ports = "8080/http, 22/tcp"
container_disk_size_gb = 10

env_vars.VAR_NAME_1 = "value1"
env_vars.VAR_NAME_2 = "value2"


[template]
model_type = "default"
model_name = "None"

[runtime]
python_version = "3.10"
requirements_path = "/requirements.txt"
```
