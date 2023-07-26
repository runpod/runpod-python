""" Allows runpod to be imported as a module. """

from pkg_resources import get_distribution, DistributionNotFound

from . import serverless
from .endpoint import Endpoint
from .endpoint import AsyncioEndpoint, AsyncioJob
from .api_wrapper.ctl_commands import(
    get_gpus, get_gpu,
    create_pod, stop_pod, resume_pod, terminate_pod
)
from .cli.config import set_credentials, check_credentials, get_credentials

api_key = None  # pylint: disable=invalid-name

api_url_base = "https://api.runpod.io"  # pylint: disable=invalid-name

endpoint_url_base = "https://api.runpod.ai/v2"  # pylint: disable=invalid-name


try:
    __version__ = get_distribution("runpod").version
except DistributionNotFound:
    __version__ = "unknown"
