"""
Helper utilities for locating package-bundled binaries.
"""

import os
from pathlib import Path
from typing import Optional


def get_binary_path(binary_name: str) -> Optional[Path]:
    """
    Locate a binary file within the runpod package.

    Search order:
    1. Environment variable: RUNPOD_BINARY_{NAME}_PATH
    2. Package location: runpod/serverless/binaries/{binary_name}

    Args:
        binary_name: Name of binary (e.g., "gpu_test")

    Returns:
        Path to binary if found and is a file, None otherwise
    """
    # Check environment variable override
    env_var = f"RUNPOD_BINARY_{binary_name.upper()}_PATH"
    if env_path := os.environ.get(env_var):
        path = Path(env_path)
        if path.exists() and path.is_file():
            return path

    # Check package location
    package_binary = Path(__file__).parent / "serverless" / "binaries" / binary_name
    if package_binary.exists() and package_binary.is_file():
        return package_binary

    return None
