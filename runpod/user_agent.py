""" User-Agent for RunPod-Python-SDK """

import os
import platform

from runpod.version import __version__ as runpod_version


def construct_user_agent():
    """Constructs the User-Agent string for the RunPod-Python-SDK

    Example:
        RunPod-Python-SDK/0.1.0 (Linux 5.4.0-54-generic; x86_64) Language/Python 3.8.5
    """
    os_info = f"{platform.system()} {platform.release()}; {platform.machine()}"
    python_version = platform.python_version()
    integration_method = os.getenv("RUNPOD_UA_INTEGRATION")

    ua_components = [
        f"RunPod-Python-SDK/{runpod_version}",
        f"({os_info})",
        f"Language/Python {python_version}",
    ]

    if integration_method:
        ua_components.append(f"Integration/{integration_method}")

    user_agent = " ".join(ua_components)
    return user_agent


USER_AGENT = construct_user_agent()
