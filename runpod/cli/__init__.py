''' Allows the CLI to be imported as a module. '''

import os
import threading

from .groups import config, ssh

STOP_EVENT = threading.Event()


# ------------------------------- Config Paths ------------------------------- #
SSH_KEY_PATH = os.path.expanduser('~/.runpod/ssh')
try:
    os.makedirs(os.path.join(SSH_KEY_PATH), exist_ok=True)
except OSError:
    pass


# --------------------------- runpod.toml Defaults --------------------------- #
BASE_DOCKER_IMAGE = 'runpod/base:0.4.0-cuda{cuda_version}'
GPU_TYPES = [
    "NVIDIA RTX A4000", "NVIDIA RTX A4500", "NVIDIA RTX A5000",
    "NVIDIA GeForce RTX 3090", "NVIDIA RTX A6000"
]
ENV_VARS = {
    "POD_INACTIVITY_TIMEOUT": "120",
    "RUNPOD_DEBUG_LEVEL": "debug",
    "UVICORN_LOG_LEVEL": "warning"
}
