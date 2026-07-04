"""deploy-time environment build: vendor dependencies into the artifact.

cold starts do no dependency resolution. everything a resource needs to
run (including the runpod runtime itself) is resolved on the build
machine into a site-packages tree that ships inside the artifact under
env/. workers put it on sys.path and start.

the exception is size-prohibitive CUDA packages (torch and friends)
that the gpu worker images already provide: vendoring them would add
gigabytes to every artifact for nothing. they are excluded here,
recorded in the manifest, and the worker bootstrap verifies they exist
in the image at startup (installing only if genuinely absent).

wheels are selected for the worker platform (manylinux/amd64) and the
target python version, not the build machine's, so a mac or windows
build produces a linux-correct environment.
"""

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
        _REQ_NAME_RE.match(spec) and re.match(r"^[A-Za-z0-9._-]+(\[[^\]]+\])?([<>=!~;].*)?$", spec)
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


def _build_wheel(spec: str, scratch_dir: Path) -> Path:
    """build a wheel from a url/path spec for binary-only vendoring."""
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
    # size-prohibitive packages come preinstalled on the builtin gpu
    # images; a custom image carries no such guarantee, so any custom
    # image in the app disables auto-exclusion and everything vendors
    auto_exclude = not any(
        h.spec.image for h in app.resources.values()
    )
    vendored, excluded = split_exclusions(
        user_requirements, exclude, auto_exclude=auto_exclude
    )

    runtime = [runtime_requirement(scratch), "cloudpickle"]
    from .spec import ResourceKind

    if any(
        h.spec.kind is ResourceKind.API for h in app.resources.values()
    ):
        runtime.append("uvicorn>=0.30")

    all_requirements = runtime + vendored
    log.info(
        "vendoring %d packages for python %s (%d excluded: %s)",
        len(all_requirements),
        python_version,
        len(excluded),
        ", ".join(excluded) or "none",
    )
    vendor(
        env_dir,
        all_requirements,
        python_version,
        progress=getattr(events, "vendor_progress", None),
    )
    if not os.environ.get("RUNPOD_PACKAGE_SPEC"):
        sync_running_package(env_dir)
    _verify_runtime(env_dir)

    return BuildResult(
        env_dir=env_dir,
        requirements=all_requirements,
        excluded=excluded,
    )
