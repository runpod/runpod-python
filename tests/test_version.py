""" Test the version module """
from unittest.mock import MagicMock, patch
from pkg_resources import DistributionNotFound

import runpod

def test_version_found():
    """Test that the version is found"""
    with patch('runpod.version.get_distribution') as mock_get_distribution:
        mock_distribution = MagicMock()
        mock_distribution.version = '1.0.0'
        mock_get_distribution.return_value = mock_distribution

        assert runpod.__version__ == '1.0.0'

def test_version_not_found():
    """Test that the version is unknown"""
    with patch('runpod.version.get_distribution') as mock_get_distribution:
        mock_get_distribution.side_effect = DistributionNotFound

        assert runpod.__version__ == 'unknown'
