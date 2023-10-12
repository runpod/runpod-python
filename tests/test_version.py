""" Test the version module """

from unittest.mock import MagicMock, patch
from pkg_resources import DistributionNotFound
# Assume the code above is in a file named version.py in the runpod package
from runpod.version import __version__

def test_version_found():
    """Test that the version is found"""
    with patch('runpod.version.get_distribution') as mock_get_distribution:
        mock_distribution = MagicMock()
        mock_distribution.version = '1.0.0'
        mock_get_distribution.return_value = mock_distribution

        # Re-import to execute the code again with the mock in place
        import runpod.version
        import importlib
        importlib.reload(runpod.version)

        assert runpod.version.__version__ == '1.0.0'

def test_version_not_found():
    """Test that the version is unknown"""
    with patch('runpod.version.get_distribution') as mock_get_distribution:
        mock_get_distribution.side_effect = DistributionNotFound

        # Re-import to execute the code again with the mock in place
        import runpod.version
        import importlib
        importlib.reload(runpod.version)

        assert runpod.version.__version__ == 'unknown'
