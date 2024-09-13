""" Tests for the user_agent module. """

import os
import unittest
from unittest.mock import patch

from runpod import __version__ as runpod_version
from runpod.user_agent import construct_user_agent


class TestConstructUserAgent(unittest.TestCase):
    """Test the construct_user_agent function."""

    @patch("runpod.user_agent.platform.system", return_value="Windows")
    @patch("runpod.user_agent.platform.release", return_value="10")
    @patch("runpod.user_agent.platform.machine", return_value="AMD64")
    @patch("runpod.user_agent.platform.python_version", return_value="3.8.10")
    def test_user_agent_without_integration(
        self, mock_python_version, mock_machine, mock_release, mock_system
    ):
        """Test the User-Agent string without specifying an integration method."""
        if "RUNPOD_UA_INTEGRATION" in os.environ:
            del os.environ["RUNPOD_UA_INTEGRATION"]

        expected_ua = f"RunPod-Python-SDK/{runpod_version} (Windows 10; AMD64) Language/Python 3.8.10"  # pylint: disable=line-too-long
        self.assertEqual(construct_user_agent(), expected_ua)

        assert mock_python_version.called
        assert mock_machine.called
        assert mock_release.called
        assert mock_system.called

    @patch("runpod.user_agent.platform.system", return_value="Linux")
    @patch("runpod.user_agent.platform.release", return_value="5.4")
    @patch("runpod.user_agent.platform.machine", return_value="x86_64")
    @patch("runpod.user_agent.platform.python_version", return_value="3.9.5")
    @patch.dict(os.environ, {"RUNPOD_UA_INTEGRATION": "SkyPilot"})
    def test_user_agent_with_integration(
        self, mock_python_version, mock_machine, mock_release, mock_system
    ):
        """Test the User-Agent string with an integration method specified."""
        expected_ua = f"RunPod-Python-SDK/{runpod_version} (Linux 5.4; x86_64) Language/Python 3.9.5 Integration/SkyPilot"  # pylint: disable=line-too-long

        os.environ["RUNPOD_UA_INTEGRATION"] = "SkyPilot"
        self.assertEqual(construct_user_agent(), expected_ua)
        os.environ.pop("RUNPOD_UA_INTEGRATION")

        assert mock_python_version.called
        assert mock_machine.called
        assert mock_release.called
        assert mock_system.called


if __name__ == "__main__":
    unittest.main()
