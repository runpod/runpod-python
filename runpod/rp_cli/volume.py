"""shared command-line helpers."""

import sys

from runpod.cli import volume

sys.modules[__name__] = volume
