"""shared account login flows."""

import sys

from runpod.auth import login

sys.modules[__name__] = login
