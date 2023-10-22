""" runpod-python version """


from importlib.metadata import version, PackageNotFoundError

def get_version():
    """ Get the version of runpod-python """""
    try:
        return version("runpod")
    except PackageNotFoundError:
        return "unknown"

__version__ = get_version()
