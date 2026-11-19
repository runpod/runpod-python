"""shared command-line helpers."""

import sys

from runpod.cli import endpoint_config

sys.modules[__name__] = endpoint_config
