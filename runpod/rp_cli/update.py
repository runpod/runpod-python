"""shared command-line helpers."""

import sys

from runpod.cli import update

sys.modules[__name__] = update
