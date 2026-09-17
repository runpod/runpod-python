"""Compatibility alias for :mod:`runpod._logger`."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("runpod._logger")
