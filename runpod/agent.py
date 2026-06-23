"""Detects which AI coding agent (if any) is driving the SDK.

Detection is based on the environment variables that agent harnesses set in
the processes they spawn. The registry mirrors Hugging Face's public
agent-harnesses list so that traffic is attributed under the same agent
identifiers across tools:
https://github.com/huggingface/huggingface.js/blob/main/packages/tasks/src/agent-harnesses.ts
"""

import os

# Each entry maps an agent identifier to the environment variables that
# identify it. Detection matches if ANY of the listed variables is set to a
# non-empty value. The list is checked in order and the first match wins;
# order matters so that more specific signals come before broader ones they
# can co-occur with (e.g. cowork before claude-code, cursor-cli before cursor).
HARNESSES = [
    ("antigravity", ["ANTIGRAVITY_AGENT"]),
    ("augment-cli", ["AUGMENT_AGENT"]),
    ("cline", ["CLINE_ACTIVE"]),
    ("cowork", ["CLAUDE_CODE_IS_COWORK"]),
    ("claude-code", ["CLAUDECODE", "CLAUDE_CODE"]),
    ("codex", ["CODEX_SANDBOX", "CODEX_CI", "CODEX_THREAD_ID"]),
    ("crush", ["CRUSH"]),
    ("gemini-cli", ["GEMINI_CLI"]),
    ("github-copilot", ["COPILOT_MODEL", "COPILOT_ALLOW_ALL", "COPILOT_GITHUB_TOKEN"]),
    ("goose", ["GOOSE_TERMINAL"]),
    ("hermes-agent", ["HERMES_SESSION_ID"]),
    ("kilo-code", ["KILOCODE_FEATURE"]),
    ("kiro", ["AGENT_CONTEXT_OUT"]),
    ("openclaw", ["OPENCLAW_SHELL"]),
    ("opencode", ["OPENCODE_CLIENT"]),
    ("pi", ["PI_CODING_AGENT"]),
    ("replit", ["REPL_ID"]),
    ("trae", ["TRAE_AI_SHELL_ID"]),
    ("zed", ["ZED_TERM"]),
    ("cursor-cli", ["CURSOR_AGENT"]),
    ("cursor", ["CURSOR_TRACE_ID"]),
]

# Generic variables any tool can set to identify itself. When set, the value is
# sanitized and used as the agent id. Only AI_AGENT is honored: a bare AGENT is
# too common in unrelated environments (CI runners, shell setups) and would
# attribute traffic to arbitrary values.
STANDARD_ENV_VARS = ["AI_AGENT"]


def known_env_vars():
    """Returns every environment variable the registry inspects, including the
    standard AI_AGENT signal. Useful for tests that need to isolate detection
    from the ambient environment.
    """
    variables = []
    for _, env_vars in HARNESSES:
        variables.extend(env_vars)
    return variables + list(STANDARD_ENV_VARS)


def _sanitize(value):
    """Keeps only User-Agent-safe characters ([A-Za-z0-9._-]), capped at 64
    characters, so an arbitrary env value cannot produce a malformed header.
    """
    value = value.strip()
    safe = []
    for char in value:
        if len(safe) >= 64:
            break
        if char.isascii() and (char.isalnum() or char in "._-"):
            safe.append(char)
    return "".join(safe)


def detect():
    """Returns the identifier of the AI coding agent driving the SDK, or an
    empty string if none is detected. Specific harness markers take priority
    over the generic AI_AGENT signal.
    """
    for agent_id, env_vars in HARNESSES:
        for env in env_vars:
            if os.getenv(env, "").strip():
                return agent_id

    for env in STANDARD_ENV_VARS:
        value = _sanitize(os.getenv(env, ""))
        if value:
            return value

    return ""


def suffix():
    """Returns the " (via <id>)" User-Agent fragment for the detected agent, or
    an empty string when none is detected. Centralizing the fragment here keeps
    the tag format identical across every client's User-Agent.
    """
    agent_id = detect()
    if agent_id:
        return f" (via {agent_id})"
    return ""
