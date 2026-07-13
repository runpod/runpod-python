"""shell launcher for booting injected runtime scripts on custom images.

builds the dockerArgs command that materializes a base64 env payload
into a file and runs it with the image's python. constraints:

  - POSIX sh only (no bash on busybox/alpine images)
  - python may not be on sh's PATH (conda/venv images), so well-known
    interpreter locations are probed
  - no base64 binary assumed: the discovered python does the decode
  - no python at all is a configuration error: the whole feature is
    running the user's python function on their image, so a pythonless
    image gets a loud, clear failure

the launcher must stay single-quote-free internally: the host parses
dockerArgs with a shell lexer and the whole script rides inside one
pair of single quotes.
"""

from pathlib import Path

# well-known interpreter locations beyond PATH
_PYTHON_CANDIDATES = (
    "python3",
    "python",
    "/usr/local/bin/python3",
    "/usr/bin/python3",
    "/opt/conda/bin/python",
    "/opt/venv/bin/python",
    "/venv/bin/python",
    "/root/.venv/bin/python",
    "/app/.venv/bin/python",
    "/usr/local/bin/python",
)


def shell_launcher(env_var: str, dest: str) -> str:
    """dockerArgs command that decodes $env_var into dest and execs it."""
    probes = " ".join(_PYTHON_CANDIDATES)
    script = (
        f'PY=""; '
        f"for c in {probes}; do "
        f'if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi; '
        f"done; "
        f'if [ -z "$PY" ]; then '
        f'echo "[shim] FATAL: no python interpreter found in this image. "'
        f'"custom images must include python3." >&2; '
        f"exit 1; fi; "
        f'echo "${env_var}" | "$PY" -c '
        f'"import base64,sys;sys.stdout.buffer.write(base64.b64decode(sys.stdin.read()))" '
        f"> {dest} && "
        f'exec "$PY" {dest}'
    )
    return f"sh -c '{script}'"


def bootstrap_source() -> str:
    """return the runtime bootstrap injected into custom images."""
    return (Path(__file__).parent.parent / "runtimes" / "bootstrap.py").read_text()


def bootstrap_docker_args() -> str:
    """return the command that starts the injected runtime bootstrap."""
    return shell_launcher("RUNPOD_BOOTSTRAP_B64", "/bootstrap.py")
