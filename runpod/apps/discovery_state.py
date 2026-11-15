"""state shared by discovery scans and invocation guards."""

import os

DISCOVERY_ENV = "RUNPOD_DISCOVERY_SCAN"


def in_discovery() -> bool:
    return bool(os.environ.get(DISCOVERY_ENV))


class DiscoveryInvocationError(Exception):
    """a handle was invoked at module level during a discovery scan."""

    def __init__(self, resource_name: str):
        super().__init__(
            f"'{resource_name}' was invoked at import time. move calls "
            f"into a function, an entrypoint, or an "
            f'`if __name__ == "__main__":` guard so importing the file '
            f"has no side effects."
        )
