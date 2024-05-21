""" Allows runpod to be imported as a module. """

import os
import logging

from . import serverless
from .api.ctl_commands import (
    create_container_registry_auth,
    create_endpoint,
    delete_endpoint,
    delete_template,
    create_pod,
    create_template,
    get_endpoints,
    get_gpu,
    get_gpus,
    get_pod,
    get_pods,
    get_user,
    resume_pod,
    stop_pod,
    terminate_pod,
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

# ------------------------------- Config Paths ------------------------------- #
SSH_KEY_PATH = os.path.expanduser("~/.runpod/ssh")


profile = "default"  # pylint: disable=invalid-name

_credentials = get_credentials(profile)
if _credentials is not None:
    api_key = _credentials["api_key"]  # pylint: disable=invalid-name
else:
    api_key = None  # pylint: disable=invalid-name

api_url_base = "https://api.runpod.io"  # pylint: disable=invalid-name

endpoint_url_base = "https://api.runpod.ai/v2"  # pylint: disable=invalid-name


# --------------------------- Force Logging Levels --------------------------- #
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("paramiko").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.WARNING)
