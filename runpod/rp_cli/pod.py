"""shared command-line helpers."""

import sys

from runpod.cli import pod

sys.modules[__name__] = pod
