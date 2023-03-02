''' Allows runpod to be imported as a module.'''

from . import serverless
from .endpoint import Endpoint

api_key = None  # pylint: disable=invalid-name

api_url_base = "https://api.runpod.io"  # pylint: disable=invalid-name

endpoint_url_base = "https://api.runpod.ai/v1"  # pylint: disable=invalid-name
