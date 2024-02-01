""" Allows runpod to be imported as a module. """

import logging
import os

from .cli.groups.config.functions import get_credentials

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
