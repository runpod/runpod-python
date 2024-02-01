import os
import platform

from runpod.version import __version__ as runpod_version

os_info = f"{platform.system()} {platform.release()}; {platform.machine()}"

USER_AGENT = f"RunPod-Python-SDK/{runpod_version}"
USER_AGENT += f" ({os_info})"
USER_AGENT += f" Language/Python {platform.python_version()}"


if os.environ.get('RUNPOD_UA_INTEGRATION') is not None:
    USER_AGENT += f" Integration/{os.environ.get('RUNPOD_UA_INTEGRATION')}"
