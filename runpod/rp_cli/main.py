"""shared command-line helpers."""

import sys

from runpod.cli import main

sys.modules[__name__] = main
