"""
Provides some of the torch.cuda functionality without requiring torch.
"""

import subprocess

from runpod._logger import RunPodLogger

log = RunPodLogger()

NVIDIA_SMI_TIMEOUT = 5


def is_available():
    """
    Returns True if CUDA is available, False otherwise.

    A hung nvidia-smi is a classic broken-GPU symptom. It is bounded so it
    cannot freeze `import runpod`, and logged at WARN so it is never confused
    with the quiet "no GPU on this machine" answer.
    """
    try:
        output = subprocess.check_output(
            ["nvidia-smi"], stderr=subprocess.DEVNULL, timeout=NVIDIA_SMI_TIMEOUT
        )
        if "NVIDIA-SMI" in output.decode():
            return True
    except subprocess.TimeoutExpired:
        log.warn(
            f"nvidia-smi did not respond within {NVIDIA_SMI_TIMEOUT}s; "
            "treating this worker as having no usable GPU"
        )
    except Exception:  # pylint: disable=broad-except
        pass
    return False
