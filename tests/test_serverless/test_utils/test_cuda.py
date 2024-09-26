"""
Unit tests for the rp_cuda module
"""

from unittest.mock import patch

from runpod.serverless.utils import rp_cuda


def test_is_available_true():
    """
    Test that is_available returns True when nvidia-smi is available
    """
    with patch(
        "subprocess.check_output", return_value=b"NVIDIA-SMI"
    ) as mock_check_output:
        assert rp_cuda.is_available() is True
    mock_check_output.assert_called_once_with("nvidia-smi", shell=True)


def test_is_available_false():
    """
    Test that is_available returns False when nvidia-smi is not available
    """
    with patch(
        "subprocess.check_output", return_value=b"Not a GPU output"
    ) as mock_check_output:
        assert rp_cuda.is_available() is False
    mock_check_output.assert_called_once_with("nvidia-smi", shell=True)


def test_is_available_exception():
    """
    Test that is_available returns False when nvidia-smi raises an exception
    """
    with patch(
        "subprocess.check_output", side_effect=Exception("Bad Command")
    ) as mock_check:
        assert rp_cuda.is_available() is False
    mock_check.assert_called_once_with("nvidia-smi", shell=True)
