"""Build a PodTemplate that injects the PR branch's runpod-python into the worker.

When RUNPOD_SDK_GIT_REF is set (e.g., in CI), the provisioned serverless endpoint
will pip install that ref before running the original start command. This ensures
the remote worker uses the PR's runpod-python, not the published PyPI version.
"""

import os
from typing import Optional

from runpod_flash import PodTemplate

QB_DEFAULT_CMD = "python handler.py"


def get_e2e_template() -> Optional[PodTemplate]:
    """Return a PodTemplate with startScript if RUNPOD_SDK_GIT_REF is set."""
    git_ref = os.environ.get("RUNPOD_SDK_GIT_REF")
    if not git_ref:
        return None

    install_url = f"git+https://github.com/runpod/runpod-python@{git_ref}"
    start_script = (
        '/bin/bash -c "'
        "apt-get update && apt-get install -y git && "
        f"pip install {install_url} --no-cache-dir && "
        f'{QB_DEFAULT_CMD}"'
    )
    return PodTemplate(startScript=start_script)
