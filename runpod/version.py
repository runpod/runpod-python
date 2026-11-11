"""runpod-python version.

The version comes first from runpod/_version.py, which setuptools-scm writes
into the package at build time and which ships in every wheel. Reading it from
the package tree keeps the version tied to the code that is actually imported.
importlib.metadata resolves whichever runpod dist-info comes first on sys.path,
which can belong to a different install. For example, a deploy artifact that
bundles the client's runpod tree (without dist-info) over a worker image that
has an older runpod installed. The metadata lookup is the fallback for trees
without a generated module, such as a source checkout that was never installed.
"""

from importlib.metadata import PackageNotFoundError, version

UNKNOWN_VERSION = "unknown"


def get_version() -> str:
    """Get the version of the runpod package tree being imported."""
    try:
        from ._version import __version__ as generated_version
    except ImportError:
        pass
    else:
        return generated_version

    try:
        return version("runpod")
    except PackageNotFoundError:
        return UNKNOWN_VERSION


__version__ = get_version()
