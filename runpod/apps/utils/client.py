"""lazy control-plane client construction."""

from typing import Optional


def default_client(api: Optional[object] = None):
    """return api when given, else a fresh AppsApiClient.

    the import is deferred so modules that only sometimes touch the
    control plane do not pull it in at import time.
    """
    if api is not None:
        return api
    from ..api import AppsApiClient

    return AppsApiClient()
