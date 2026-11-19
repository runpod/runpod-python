"""shared command-line helpers."""

import sys

from runpod.cli import options

sys.modules[__name__] = options
