"""shared command-line helpers."""

import sys

from runpod.cli import api

sys.modules[__name__] = api
