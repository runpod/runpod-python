""" runpod-python version """

from pkg_resources import get_distribution, DistributionNotFound

def get_version():
    """ Get the version of runpod-python """""
    try:
        return get_distribution("runpod").version
    except DistributionNotFound:
        return "unknown"

__version__ = get_version()
