"""Compatibility alias for :mod:`runpod._health.gpu`."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("runpod._health.gpu")
