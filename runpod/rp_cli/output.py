"""shared command-line helpers."""

import sys

from runpod.cli import output

sys.modules[__name__] = output
