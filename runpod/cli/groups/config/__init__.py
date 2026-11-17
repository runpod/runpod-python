"""credential imports for runpod-flash."""

import sys

from runpod.auth import credentials as functions

sys.modules[f"{__name__}.functions"] = functions
