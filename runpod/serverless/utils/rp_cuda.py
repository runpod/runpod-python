"""
Provides some of the torch.cuda functionality without requiring torch.
"""

import subprocess


def is_available():
    '''
    Returns True if CUDA is available, False otherwise.
    '''
    try:
        output = subprocess.check_output("nvidia-smi", shell=True)
        if "NVIDIA-SMI" in output.decode():
            return True
    except Exception:  # pylint: disable=broad-except
        pass
    return False
