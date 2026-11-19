"""shared command-line helpers."""

import sys

from runpod.cli import endpoint

sys.modules[__name__] = endpoint
