""" Allows runpod to be imported as a module. """

import os

from .version import __version__
from . import serverless
from .endpoint import Endpoint
from .endpoint import AsyncioEndpoint, AsyncioJob
from .api.ctl_commands import(
    get_user, update_user_settings,
    get_gpus, get_gpu,
    get_pods, get_pod,
    create_pod, stop_pod, resume_pod, terminate_pod
)
from .cli.groups.config.functions import set_credentials, check_credentials, get_credentials


# ------------------------------- Config Paths ------------------------------- #
SSH_KEY_FOLDER = os.path.expanduser('~/.runpod/ssh')


profile = "default"  # pylint: disable=invalid-name

_credentials = get_credentials(profile)
if _credentials is not None:
    api_key = _credentials['api_key']  # pylint: disable=invalid-name
else:
    api_key = None  # pylint: disable=invalid-name

api_url_base = "https://api.runpod.io"  # pylint: disable=invalid-name

endpoint_url_base = "https://api.runpod.ai/v2"  # pylint: disable=invalid-name
