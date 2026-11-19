"""shared command-line helpers."""

import sys

from runpod.cli import compute

sys.modules[__name__] = compute
