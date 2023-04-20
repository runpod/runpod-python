""" Allows runpod to be imported as a module. """

from . import serverless
from .endpoint import Endpoint
from .endpoint import AsyncioEndpoint, AsyncioJob
from .api_wrapper.ctl_commands import(
    get_gpus, get_gpu,
    create_pod, stop_pod, resume_pod, terminate_pod
)

api_key = None  # pylint: disable=invalid-name

api_url_base = "https://api.runpod.io"  # pylint: disable=invalid-name

endpoint_url_base = "https://api.runpod.ai/v1"  # pylint: disable=invalid-name
