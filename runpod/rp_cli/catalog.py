"""shared command-line helpers."""

import sys

from runpod.cli import catalog

sys.modules[__name__] = catalog
