"""shell launcher for booting injected runtime scripts on custom images.

builds the dockerArgs command that materializes a base64 env payload
into a file and runs it. constraints, in order of hostility:

  - no bash: POSIX sh only (busybox/alpine images)
  - no cpython on PATH: probe well-known interpreter locations (conda,
    venv, uv, system) before giving up
  - no base64 binary: the discovered python does the decode
  - no python at all: download a standalone build via curl or wget

the launcher must stay single-quote-free internally: the host parses
dockerArgs with a shell lexer and the whole script rides inside one
pair of single quotes.
"""

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

# standalone cpython for images with no interpreter at all; the musl
# variant covers alpine, detected via the musl loader path
_STANDALONE_BASE = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    "20250409/cpython-3.12.10%2B20250409-x86_64-unknown-linux-"
)
_STANDALONE_GNU = f"{_STANDALONE_BASE}gnu-install_only.tar.gz"
_STANDALONE_MUSL = f"{_STANDALONE_BASE}musl-install_only.tar.gz"


def shell_launcher(env_var: str, dest: str) -> str:
    """dockerArgs command that decodes $env_var into dest and execs it."""
    probes = " ".join(_PYTHON_CANDIDATES)
    script = (
        f'PY=""; '
        f"for c in {probes}; do "
        f'if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi; '
        f"done; "
        f'if [ -z "$PY" ]; then '
        f'echo "[shim] no python found, downloading standalone build" >&2; '
        f"if ls /lib/ld-musl-* >/dev/null 2>&1; then URL={_STANDALONE_MUSL}; "
        f"else URL={_STANDALONE_GNU}; fi; "
        f"mkdir -p /tmp/rp-python && cd /tmp/rp-python && "
        f'( command -v curl >/dev/null 2>&1 && curl -fsSL "$URL" -o py.tgz '
        f'|| wget -q "$URL" -O py.tgz ) && '
        f"tar -xzf py.tgz && PY=/tmp/rp-python/python/bin/python3; "
        f"fi; "
        f'if [ -z "$PY" ]; then '
        f'echo "[shim] FATAL: no python interpreter available and no curl/wget "'
        f'"to fetch one. use an image with python3, curl, or wget." >&2; '
        f"exit 1; fi; "
        f'echo "${env_var}" | "$PY" -c '
        f'"import base64,sys;sys.stdout.buffer.write(base64.b64decode(sys.stdin.read()))" '
        f"> {dest} && "
        f'exec "$PY" {dest}'
    )
    return f"sh -c '{script}'"
