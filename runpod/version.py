""" runpod-python version """

from pkg_resources import get_distribution, DistributionNotFound

try:
    __version__ = get_distribution("runpod").version
except DistributionNotFound:
    __version__ = "unknown"
