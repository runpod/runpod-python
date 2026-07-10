"""deploy-time environment build: vendor dependencies into the artifact."""

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from .errors import AppError
from .images import DEFAULT_PYTHON_VERSION, SUPPORTED_PYTHON_VERSIONS

log = logging.getLogger(__name__)

# manylinux tags matching runpod worker glibc, newest first
MANYLINUX_PLATFORMS = (
    "manylinux_2_28_x86_64",
    "manylinux_2_17_x86_64",
    "manylinux2014_x86_64",
)

# CUDA-scale packages provided by the gpu worker images. excluded by
# size, not by image contents: do not add packages just because an
# image happens to ship them.
SIZE_PROHIBITIVE_PACKAGES = frozenset(
    {
        "torch",
        "torchvision",
        "torchaudio",
        "triton",
    }
)

# the worker runtime's actual dependency closure, a small subset of the
# runpod package's full dependency list. the runpod package is vendored
# without its dependencies (sdk/cli-only packages like boto3, paramiko,
# and cryptography never load in a worker), so these must be vendored
# explicitly to keep a dependency-free worker small.
RUNTIME_REQUIREMENTS = (
    "aiohttp[speedups]",
    "aiohttp-retry",
    "backoff",
    "cloudpickle",
    "py-cpuinfo",
    "requests",
    "tomli",
    "tomlkit",
    "tqdm-loggable",
)

# api workers additionally serve over fastapi/uvicorn
API_RUNTIME_REQUIREMENTS = ("fastapi[standard]", "uvicorn>=0.30")

# runpod's serverless limit for build artifacts
MAX_ARTIFACT_MB = 1500

_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


class BuildError(AppError):
    """environment vendoring failed."""


@dataclass
class BuildResult:
    """output of build_environment."""

    env_dir: Path
    requirements: List[str] = field(default_factory=list)
    excluded: List[str] = field(default_factory=list)


def requirement_name(requirement: str) -> str:
    """distribution name from a requirement string (drops extras/pins)."""
    match = _REQ_NAME_RE.match(requirement)
    return (match.group(1) if match else requirement).lower().replace("_", "-")


def collect_requirements(project_root: Path, app) -> List[str]:
    """project requirements.txt plus per-resource dependencies, deduped."""
    requirements: List[str] = []

    req_file = project_root / "requirements.txt"
    if req_file.exists():
        for line in req_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("-"):
                requirements.append(line)

    for handle in app.resources.values():
        for dep in handle.spec.dependencies or []:
            requirements.append(dep)

    seen = set()
    unique = []
    for req in requirements:
        key = requirement_name(req)
        if key not in seen:
            seen.add(key)
            unique.append(req)
    return unique


def split_exclusions(
    requirements: List[str],
    extra_excludes: Optional[List[str]] = None,
    *,
    auto_exclude: bool = True,
) -> Tuple[List[str], List[str]]:
    """(vendored, excluded-names). excluded packages resolve from the
    worker image at runtime instead of the artifact.

    auto_exclude applies the size-prohibitive set; it is only sound
    when the workers run on builtin gpu images that preinstall those
    packages. custom images vendor everything unless the user excludes
    explicitly.
    """
    excluded_names = {requirement_name(e) for e in (extra_excludes or [])}
    if auto_exclude:
        excluded_names |= SIZE_PROHIBITIVE_PACKAGES
    kept: List[str] = []
    excluded: List[str] = []
    for req in requirements:
        name = requirement_name(req)
        if name in excluded_names:
            excluded.append(name)
        else:
            kept.append(req)
    # size-prohibitive packages are expected from the image whether or
    # not the user listed them, so the manifest always records the auto
    # set that user requirements referenced
    return kept, sorted(set(excluded))


def _is_pinnable_name(spec: str) -> bool:
    """true if the spec is a plain requirement (name, optional pin), as
    opposed to a url, git ref, or filesystem path."""
    return not any(token in spec for token in ("/", "\\", ":", "@")) or bool(
        _REQ_NAME_RE.match(spec)
        and re.match(r"^[A-Za-z0-9._-]+(\[[^\]]+\])?([<>=!~;].*)?$", spec)
    )


def runtime_requirement(scratch_dir: Path) -> str:
    """the runpod runtime spec to vendor into the artifact.

    RUNPOD_PACKAGE_SPEC overrides the published package (version pins,
    git refs, tarball urls, local checkouts). non-index specs are
    built into a wheel first so the platform-targeted vendoring
    install can stay binary-only.

    without an override the published package is vendored for its
    dependency closure, then sync_running_package overlays the
    client's own package tree so the worker always runs exactly the
    client's code regardless of what pypi has.
    """
    spec = os.environ.get("RUNPOD_PACKAGE_SPEC", "runpod")
    if _is_pinnable_name(spec):
        return spec
    return str(_build_wheel(spec, scratch_dir))


def sync_running_package(env_dir: Path) -> None:
    """overlay the running runpod package onto the vendored env.

    the package is pure python (py3-none-any), so the client's
    installed tree is valid on the worker platform. this pins the
    vendored runtime to the exact client version: wire protocols,
    manifest handling, and worker code can never drift, and prerelease
    clients work before their version reaches pypi.
    """
    import runpod as _runpod

    src = Path(_runpod.__file__).resolve().parent
    dest = env_dir / "runpod"
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        dirs_exist_ok=True,
    )


def _ensure_pip() -> None:
    """make `python -m pip` work in venvs created without pip (uv)."""
    probe = subprocess.run(
        [sys.executable, "-m", "pip", "--version"],
        capture_output=True,
    )
    if probe.returncode == 0:
        return
    bootstrap = subprocess.run(
        [sys.executable, "-m", "ensurepip", "--upgrade"],
        capture_output=True,
        text=True,
    )
    if bootstrap.returncode != 0:
        raise BuildError(
            f"pip is not available in this environment and ensurepip "
            f"failed: {bootstrap.stderr[-500:]}"
        )


def _build_wheel(spec: str, scratch_dir: Path) -> Path:
    """build a wheel from a url/path spec for binary-only vendoring."""
    _ensure_pip()
    wheel_dir = scratch_dir / "wheels"
    wheel_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    # tarball specs (e.g. github archive urls) have no .git for
    # setuptools-scm version inference; pretend a version so they build
    env.setdefault("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_RUNPOD", "0.0.0.dev0")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "-q",
            "-w",
            str(wheel_dir),
            spec,
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise BuildError(
            f"failed to build wheel from RUNPOD_PACKAGE_SPEC={spec!r}: "
            f"{result.stderr[-2000:]}"
        )
    wheels = sorted(wheel_dir.glob("runpod-*.whl"))
    if not wheels:
        raise BuildError(
            f"pip wheel produced no runpod wheel from {spec!r} "
            f"(found: {[w.name for w in wheel_dir.glob('*.whl')]})"
        )
    return wheels[-1]


def vendor(
    env_dir: Path,
    requirements: List[str],
    python_version: str,
    *,
    no_deps: bool = False,
    progress: Optional[callable] = None,
) -> None:
    """resolve requirements into env_dir for the worker platform.

    progress, if given, is called as progress(count, package) each time
    pip starts resolving another distribution.
    """
    if not requirements:
        return
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--no-color",
        "--target",
        str(env_dir),
        "--python-version",
        python_version,
        "--implementation",
        "cp",
        "--only-binary",
        ":all:",
    ]
    for platform in MANYLINUX_PLATFORMS:
        cmd.extend(["--platform", platform])
    if no_deps:
        cmd.append("--no-deps")
    cmd.extend(requirements)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    count = 0
    for line in proc.stdout:
        line = line.strip()
        # pip emits one "Collecting <dist>" per resolved distribution
        if line.startswith("Collecting "):
            count += 1
            if progress is not None:
                name = re.split(r"[><=!~\[ (]", line[11:], maxsplit=1)[0]
                progress(count, name)
    stderr = proc.stderr.read()
    proc.wait()
    if proc.returncode != 0:
        raise BuildError(
            f"dependency vendoring failed: {stderr[-3000:]}\n"
            f"packages without linux wheels cannot be vendored; "
            f"exclude them (they must then come from the image)"
        )


def _verify_runtime(env_dir: Path) -> None:
    """the vendored runpod must include the worker runtimes."""
    if not (env_dir / "runpod" / "runtimes").is_dir():
        raise BuildError(
            "the vendored runpod package has no runtimes modules; the "
            "resolved version predates them. set RUNPOD_PACKAGE_SPEC to a "
            "version that includes runpod.runtimes"
        )


def build_environment(
    app,
    project_root: Path,
    *,
    python_version: str = DEFAULT_PYTHON_VERSION,
    exclude: Optional[List[str]] = None,
    scratch_dir: Optional[Path] = None,
    events: Optional[object] = None,
) -> BuildResult:
    """vendor the app's full runtime environment into a directory tree."""
    if python_version not in SUPPORTED_PYTHON_VERSIONS:
        raise BuildError(
            f"python {python_version} is not supported "
            f"(supported: {', '.join(SUPPORTED_PYTHON_VERSIONS)})"
        )
    scratch = scratch_dir or Path(tempfile.mkdtemp(prefix="rp-build-"))
    env_dir = scratch / "env"
    env_dir.mkdir(parents=True, exist_ok=True)

    user_requirements = collect_requirements(project_root, app)
    # size-prohibitive packages come preinstalled only on the builtin
    # gpu images. custom images carry no guarantee, and builtin cpu
    # images ship without them, so every resource must be gpu-on-builtin
    # for auto-exclusion to be sound (the artifact is shared app-wide)
    auto_exclude = all(
        h.spec.gpu and not h.spec.image for h in app.resources.values()
    )
    vendored, excluded = split_exclusions(
        user_requirements, exclude, auto_exclude=auto_exclude
    )

    from .spec import ResourceKind

    runtime_deps = list(RUNTIME_REQUIREMENTS)
    if any(h.spec.kind is ResourceKind.API for h in app.resources.values()):
        runtime_deps.extend(API_RUNTIME_REQUIREMENTS)

    package_spec = os.environ.get("RUNPOD_PACKAGE_SPEC")
    progress = getattr(events, "vendor_progress", None)

    # the runpod package ships without its own dependency closure; the
    # worker runtime pulls only runtime_deps. with an override the pinned
    # version is installed no-deps; otherwise the client's own package
    # tree is overlaid below, so no index install of runpod is needed.
    if package_spec:
        vendor(
            env_dir,
            [runtime_requirement(scratch)],
            python_version,
            no_deps=True,
            progress=progress,
        )

    all_requirements = runtime_deps + vendored
    log.info(
        "vendoring %d packages for python %s (%d excluded: %s)",
        len(all_requirements),
        python_version,
        len(excluded),
        ", ".join(excluded) or "none",
    )
    vendor(env_dir, all_requirements, python_version, progress=progress)

    if not package_spec:
        sync_running_package(env_dir)
    _verify_runtime(env_dir)

    return BuildResult(
        env_dir=env_dir,
        requirements=all_requirements,
        excluded=excluded,
    )
