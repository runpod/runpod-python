""" Test the version module """

from importlib.metadata import PackageNotFoundError
from unittest.mock import patch

from runpod.version import get_version


def test_version_found():
    """Test that the version is found"""
    with patch("runpod.version.version") as mock_get_version:
        mock_get_version.return_value = "1.0.0"
        assert get_version() == "1.0.0"
        assert mock_get_version.called


def test_version_not_found():
    """Test that the version is not found"""
    with patch("runpod.version.version") as mock_get_version:
        mock_get_version.side_effect = PackageNotFoundError
        assert get_version() == "unknown"
        assert mock_get_version.called
