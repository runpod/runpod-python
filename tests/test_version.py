""" Test the version module """

from unittest.mock import patch, Mock
from pkg_resources import DistributionNotFound
from runpod.version import get_version

def test_version_found():
    """ Test that the version is found """
    with patch('runpod.version.get_distribution') as mock_get_distribution:
        mock_get_distribution.return_value = Mock(version='1.0.0')
        assert get_version() == '1.0.0'
        assert mock_get_distribution.called

def test_version_not_found():
    """ Test that the version is not found """
    with patch('runpod.version.get_distribution') as mock_get_distribution:
        mock_get_distribution.side_effect = DistributionNotFound
        assert get_version() == 'unknown'
        assert mock_get_distribution.called
