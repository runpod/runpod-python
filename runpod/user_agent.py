"""User-Agent for RunPod-Python-SDK"""

import os
import platform
import re
from typing import Optional

from runpod import agent
from runpod.version import __version__ as runpod_version


def construct_user_agent(integration: Optional[str] = None):
    """Constructs the User-Agent string for the RunPod-Python-SDK

    Example:
        RunPod-Python-SDK/0.1.0 (Linux 5.4.0-54-generic; x86_64) Language/Python 3.8.5

    explicit integrations precede the user's RUNPOD_UA_INTEGRATION tag.
    """
    if integration is not None and not re.fullmatch(r"[A-Za-z0-9._-]+", integration):
        raise ValueError(
            "integration must be a non-empty token using A-Z, a-z, 0-9, ., _, or -"
        )
    os_info = f"{platform.system()} {platform.release()}; {platform.machine()}"
    python_version = platform.python_version()
    integration_method = os.getenv("RUNPOD_UA_INTEGRATION")

    ua_components = [
        f"RunPod-Python-SDK/{runpod_version}",
        f"({os_info})",
        f"Language/Python {python_version}",
    ]

    if integration:
        ua_components.append(f"Integration/{integration}")
    if integration_method and integration_method != integration:
        ua_components.append(f"Integration/{integration_method}")

    agent_suffix = agent.suffix()
    if agent_suffix:
        ua_components.append(agent_suffix.strip())

    user_agent = " ".join(ua_components)
    return user_agent


USER_AGENT = construct_user_agent()
APPS_USER_AGENT = construct_user_agent(integration="apps")
