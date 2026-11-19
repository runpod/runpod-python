"""shared command-line helpers."""

import sys

from runpod.cli import console

sys.modules[__name__] = console
