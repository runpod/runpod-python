''' Allows runpod to be imported as a module.'''

from . import serverless
from .endpoint import Endpoint

api_key = None  # pylint: disable=constant-name

api_url_base = "https://api.runpod.io"  # pylint: disable=constant-name

endpoint_url_base = "https://api.runpod.ai/v1"  # pylint: disable=constant-name
