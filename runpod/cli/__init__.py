''' Allows the CLI to be imported as a module. '''

from .groups import config, ssh

# --------------------------- runpod.toml Defaults --------------------------- #
BASE_DOCKER_IMAGE = 'runpod/base:0.3.1-cuda{cuda_version}'
GPU_TYPES = [
    "NVIDIA RTX A4000", "NVIDIA RTX A4500", "NVIDIA RTX A5000",
    "NVIDIA GeForce RTX 3090", "NVIDIA RTX A6000"
]
ENV_VARS = {
    "RUNPOD_DEBUG_LEVEL": "debug",
    "UVICORN_LOG_LEVEL": "warning"
}
