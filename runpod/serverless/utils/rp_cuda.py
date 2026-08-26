"""
Provides some of the torch.cuda functionality without requiring torch.
"""

import subprocess


def is_available():
    """
    Returns True if CUDA is available, False otherwise.
    """
    try:
        # Bounded: this runs at `import runpod` on real workers, where a wedged
        # nvidia-smi must not hang the boot forever.
        output = subprocess.check_output(
            ["nvidia-smi"], stderr=subprocess.DEVNULL, timeout=5
        )
        if "NVIDIA-SMI" in output.decode():
            return True
    except Exception:  # pylint: disable=broad-except
        pass
    return False
