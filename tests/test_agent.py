"""Tests for the agent detection module."""

import os
import unittest
from unittest.mock import patch

from runpod import agent


def _clean_env():
    """Returns an environment dict with every known agent signal removed."""
    env = dict(os.environ)
    for key in agent.known_env_vars():
        env.pop(key, None)
    return env


class TestDetect(unittest.TestCase):
    """Test the agent.detect function."""

    def test_no_agent(self):
        """No agent env vars set means no detection."""
        with patch.dict(os.environ, _clean_env(), clear=True):
            self.assertEqual(agent.detect(), "")
            self.assertEqual(agent.suffix(), "")

    def test_claude_code(self):
        """CLAUDECODE detects claude-code."""
        env = _clean_env()
        env["CLAUDECODE"] = "1"
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(agent.detect(), "claude-code")
            self.assertEqual(agent.suffix(), " (via claude-code)")

    def test_claude_code_alternate_var(self):
        """CLAUDE_CODE also detects claude-code."""
        env = _clean_env()
        env["CLAUDE_CODE"] = "1"
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(agent.detect(), "claude-code")

    def test_cursor(self):
        """CURSOR_TRACE_ID detects cursor."""
        env = _clean_env()
        env["CURSOR_TRACE_ID"] = "abc123"
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(agent.detect(), "cursor")

    def test_cursor_cli_before_cursor(self):
        """cursor-cli is more specific and wins over cursor when both are set."""
        env = _clean_env()
        env["CURSOR_AGENT"] = "1"
        env["CURSOR_TRACE_ID"] = "abc123"
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(agent.detect(), "cursor-cli")

    def test_cowork_before_claude_code(self):
        """cowork is more specific and wins over claude-code when both are set."""
        env = _clean_env()
        env["CLAUDE_CODE_IS_COWORK"] = "1"
        env["CLAUDECODE"] = "1"
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(agent.detect(), "cowork")

    def test_codex(self):
        """A Codex marker detects codex."""
        env = _clean_env()
        env["CODEX_THREAD_ID"] = "t-1"
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(agent.detect(), "codex")

    def test_gemini_cli(self):
        """GEMINI_CLI detects gemini-cli."""
        env = _clean_env()
        env["GEMINI_CLI"] = "1"
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(agent.detect(), "gemini-cli")

    def test_empty_value_not_detected(self):
        """An env var set to empty string does not count as detection."""
        env = _clean_env()
        env["CLAUDECODE"] = ""
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(agent.detect(), "")

    def test_ai_agent_generic_fallback(self):
        """The generic AI_AGENT signal is used when no harness matches."""
        env = _clean_env()
        env["AI_AGENT"] = "my-custom-agent"
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(agent.detect(), "my-custom-agent")

    def test_harness_wins_over_ai_agent(self):
        """A specific harness marker takes priority over the generic AI_AGENT."""
        env = _clean_env()
        env["AI_AGENT"] = "my-custom-agent"
        env["CLAUDECODE"] = "1"
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(agent.detect(), "claude-code")

    def test_ai_agent_sanitized(self):
        """AI_AGENT values are sanitized to User-Agent-safe characters."""
        env = _clean_env()
        env["AI_AGENT"] = "bad value (with) chars!"
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(agent.detect(), "badvaluewithchars")

    def test_ai_agent_length_capped(self):
        """AI_AGENT values are capped at 64 characters."""
        env = _clean_env()
        env["AI_AGENT"] = "a" * 200
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(agent.detect(), "a" * 64)


if __name__ == "__main__":
    unittest.main()
