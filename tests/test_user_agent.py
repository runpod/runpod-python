""" Tests for the user_agent module. """

import os
import unittest
from unittest.mock import patch

from runpod import __version__ as runpod_version
from runpod import agent
from runpod.user_agent import construct_user_agent


def _agent_env_keys():
    """Every env var that could inject an agent tag, plus the integration var."""
    return agent.known_env_vars() + ["RUNPOD_UA_INTEGRATION"]


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
        saved = {k: os.environ.pop(k) for k in _agent_env_keys() if k in os.environ}

        expected_ua = f"RunPod-Python-SDK/{runpod_version} (Windows 10; AMD64) Language/Python 3.8.10"  # pylint: disable=line-too-long
        self.assertEqual(construct_user_agent(), expected_ua)

        os.environ.update(saved)

        assert mock_python_version.called
        assert mock_machine.called
        assert mock_release.called
        assert mock_system.called

    @patch("runpod.user_agent.platform.system", return_value="Linux")
    @patch("runpod.user_agent.platform.release", return_value="5.4")
    @patch("runpod.user_agent.platform.machine", return_value="x86_64")
    @patch("runpod.user_agent.platform.python_version", return_value="3.9.5")
    def test_user_agent_with_integration(
        self, mock_python_version, mock_machine, mock_release, mock_system
    ):
        """Test the User-Agent string with an integration method specified."""
        saved = {k: os.environ.pop(k) for k in _agent_env_keys() if k in os.environ}
        os.environ["RUNPOD_UA_INTEGRATION"] = "SkyPilot"

        expected_ua = f"RunPod-Python-SDK/{runpod_version} (Linux 5.4; x86_64) Language/Python 3.9.5 Integration/SkyPilot"  # pylint: disable=line-too-long
        self.assertEqual(construct_user_agent(), expected_ua)

        os.environ.pop("RUNPOD_UA_INTEGRATION")
        os.environ.update(saved)

        assert mock_python_version.called
        assert mock_machine.called
        assert mock_release.called
        assert mock_system.called


    @patch("runpod.user_agent.platform.system", return_value="Linux")
    @patch("runpod.user_agent.platform.release", return_value="5.4")
    @patch("runpod.user_agent.platform.machine", return_value="x86_64")
    @patch("runpod.user_agent.platform.python_version", return_value="3.9.5")
    def test_user_agent_with_claude_code(
        self, mock_python_version, mock_machine, mock_release, mock_system
    ):
        """Test the User-Agent string includes the claude-code agent tag."""
        saved = {k: os.environ.pop(k) for k in _agent_env_keys() if k in os.environ}
        os.environ["CLAUDECODE"] = "1"

        expected_ua = f"RunPod-Python-SDK/{runpod_version} (Linux 5.4; x86_64) Language/Python 3.9.5 (via claude-code)"
        self.assertEqual(construct_user_agent(), expected_ua)

        os.environ.pop("CLAUDECODE", None)
        os.environ.update(saved)

    @patch("runpod.user_agent.platform.system", return_value="Linux")
    @patch("runpod.user_agent.platform.release", return_value="5.4")
    @patch("runpod.user_agent.platform.machine", return_value="x86_64")
    @patch("runpod.user_agent.platform.python_version", return_value="3.9.5")
    def test_user_agent_without_claude_code(
        self, mock_python_version, mock_machine, mock_release, mock_system
    ):
        """Test the User-Agent string excludes agent tag when no env var is set."""
        saved = {k: os.environ.pop(k) for k in _agent_env_keys() if k in os.environ}

        ua = construct_user_agent()
        self.assertNotIn("(via ", ua)

        os.environ.update(saved)

    @patch("runpod.user_agent.platform.system", return_value="Linux")
    @patch("runpod.user_agent.platform.release", return_value="5.4")
    @patch("runpod.user_agent.platform.machine", return_value="x86_64")
    @patch("runpod.user_agent.platform.python_version", return_value="3.9.5")
    def test_user_agent_with_other_agent(
        self, mock_python_version, mock_machine, mock_release, mock_system
    ):
        """Test the User-Agent string includes a non-Claude agent tag (e.g. cursor)."""
        saved = {k: os.environ.pop(k) for k in _agent_env_keys() if k in os.environ}
        os.environ["CURSOR_TRACE_ID"] = "abc123"

        ua = construct_user_agent()
        self.assertIn("(via cursor)", ua)

        os.environ.pop("CURSOR_TRACE_ID", None)
        os.environ.update(saved)


if __name__ == "__main__":
    unittest.main()
