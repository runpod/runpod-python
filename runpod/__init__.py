""" Allows runpod to be imported as a module. """

import logging

from . import serverless
from .endpoint import Endpoint
from .endpoint import AsyncioEndpoint, AsyncioJob
from .version import __version__
from .api.ctl_commands import(
    get_gpus, get_gpu,
    get_pods, get_pod,
    create_pod, stop_pod, resume_pod, terminate_pod
)
from .cli.config import set_credentials, check_credentials, get_credentials


profile = "default"  # pylint: disable=invalid-name

_credentials = get_credentials(profile)
if _credentials is not None:
    api_key = _credentials['api_key']  # pylint: disable=invalid-name
else:
    api_key = None  # pylint: disable=invalid-name

api_url_base = "https://api.runpod.io"  # pylint: disable=invalid-name

endpoint_url_base = "https://api.runpod.ai/v2"  # pylint: disable=invalid-name


# --------------------------- Force Logging Levels --------------------------- #
logging.getLogger("urllib3").setLevel(logging.WARNING)
