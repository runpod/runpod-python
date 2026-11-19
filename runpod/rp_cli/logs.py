"""shared command-line helpers."""

import sys

from runpod.cli import logs

sys.modules[__name__] = logs
