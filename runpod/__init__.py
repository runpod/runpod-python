""" Allows runpod to be imported as a module. """

import logging
import os

from . import serverless
from .api.ctl_commands import (
    create_container_registry_auth,
    create_endpoint,
    create_pod,
    create_template,
    delete_container_registry_auth,
    get_endpoints,
    get_gpu,
    get_gpus,
    get_pod,
    get_pods,
    get_user,
    resume_pod,
    stop_pod,
    terminate_pod,
    update_container_registry_auth,
    update_endpoint_template,
    update_user_settings,
)
from .cli.groups.config.functions import (
    check_credentials,
    get_credentials,
    set_credentials,
)
from .endpoint import AsyncioEndpoint, AsyncioJob, Endpoint
from .serverless.modules.rp_logger import RunPodLogger
from .version import __version__

__all__ = [
    # API functions
    "create_container_registry_auth",
    "create_endpoint", 
    "create_pod",
    "create_template",
    "delete_container_registry_auth",
    "get_endpoints",
    "get_gpu",
    "get_gpus", 
    "get_pod",
    "get_pods",
    "get_user",
    "resume_pod",
    "stop_pod",
    "terminate_pod",
    "update_container_registry_auth",
    "update_endpoint_template",
    "update_user_settings",
    # Config functions
    "check_credentials",
    "get_credentials", 
    "set_credentials",
    # Endpoint classes
    "AsyncioEndpoint",
    "AsyncioJob",
    "Endpoint",
    # Serverless module
    "serverless",
    # Logger class
    "RunPodLogger",
    # Version
    "__version__",
    # Module variables
    "SSH_KEY_PATH",
    "profile",
    "api_key", 
    "endpoint_url_base"
]

# ------------------------------- Config Paths ------------------------------- #
SSH_KEY_PATH = os.path.expanduser("~/.runpod/ssh")


profile = "default"  # pylint: disable=invalid-name

_credentials = get_credentials(profile)
if _credentials is not None:
    api_key = _credentials["api_key"]  # pylint: disable=invalid-name
else:
    api_key = None  # pylint: disable=invalid-name

endpoint_url_base = os.environ.get(
    "RUNPOD_ENDPOINT_BASE_URL", "https://api.runpod.ai/v2"
)  # pylint: disable=invalid-name


# --------------------------- Force Logging Levels --------------------------- #
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("paramiko").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.WARNING)
