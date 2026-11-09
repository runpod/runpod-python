"""shell launcher for runtime modules on custom images.

constraints:

  - posix sh only
  - python may live outside the image's path
  - the runtime package may already be baked into the image
  - missing runtimes install from the latest github release source archive
  - package overrides support prerelease and pinned runtime builds

commands stay single-quote-free internally because the host parses
`dockerArgs` with a shell lexer and wraps the script in single quotes.
"""

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

_RUNTIME_MODULES = {
    "api": "runpod_sdk_runtime.bootstrap",
    "queue": "runpod_sdk_runtime.bootstrap",
    "task": "runpod_sdk_runtime.task.runner",
}

def runtime_launcher(kind: str) -> str:
    """dockerArgs command that installs and starts a runtime module."""
    try:
        module = _RUNTIME_MODULES[kind]
    except KeyError as exc:
        raise ValueError(f"unknown runtime kind: {kind}") from exc

    probes = " ".join(_PYTHON_CANDIDATES)
    release_lookup = (
        "import urllib.request\n"
        'with urllib.request.urlopen("https://github.com/runpod-workers/sdk-runtime/releases/latest", timeout=30) as response:\n'
        "    url = response.geturl()\n"
        'prefix = "https://github.com/runpod-workers/sdk-runtime/releases/tag/"\n'
        "if not url.startswith(prefix) or not url[len(prefix):]:\n"
        '    raise RuntimeError("could not resolve the latest runtime release")\n'
        'print("runpod-sdk-runtime @ https://github.com/runpod-workers/sdk-runtime/archive/refs/tags/" + url[len(prefix):] + ".tar.gz")'
    ).replace('"', '\\"')
    script = (
        f'PY=""; '
        f"for c in {probes}; do "
        f'if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi; '
        f"done; "
        f'if [ -z "$PY" ]; then '
        f'echo "[shim] FATAL: no python interpreter found in this image. "'
        f'"custom images must include python3." >&2; '
        f"exit 1; fi; "
        f'export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_RUNPOD="${{SETUPTOOLS_SCM_PRETEND_VERSION_FOR_RUNPOD:-0.0.0.dev0}}"; '
        f'RUNTIME_SPEC="${{RUNPOD_RUNTIME_PACKAGE_SPEC:-}}"; '
        f'if [ -n "${{RUNPOD_RUNTIME_PACKAGE_SPEC:-}}" ] || '
        f'! "$PY" -c "import runpod_sdk_runtime" >/dev/null 2>&1; then '
        f'if [ -z "$RUNTIME_SPEC" ]; then '
        f'RUNTIME_SPEC=$("$PY" -c "{release_lookup}") || exit 1; '
        f"fi; "
        f'"$PY" -m pip install -q --upgrade "$RUNTIME_SPEC" || exit 1; '
        f"fi; "
        f'SDK_SPEC="${{RUNPOD_PACKAGE_SPEC:-runpod}}"; '
        f'if [ -n "${{RUNPOD_PACKAGE_SPEC:-}}" ] || '
        f'! "$PY" -c "import runpod" >/dev/null 2>&1; then '
        f'"$PY" -m pip install -q --upgrade "$SDK_SPEC" || exit 1; '
        f"fi; "
        f'exec "$PY" -m {module}'
    )
    return f"sh -c '{script}'"
