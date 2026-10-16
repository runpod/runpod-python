"""deploy-time environment build: vendor dependencies into the artifact."""

import csv
import logging
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from collections import deque
from email.parser import HeaderParser
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

# the worker's runpod dependency closure, a small subset of the full sdk
# dependency list. the package is vendored without sdk/cli-only packages
# that never load in a worker.
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

# a name only when followed by extras, a specifier, a marker, a direct
# reference, or the end (not a url, path, or archive filename)
_STRICT_REQ_NAME_RE = re.compile(
    r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(?:$|[<>=!~;@(])"
)


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


def _requirements_txt(project_root: Path) -> List[str]:
    """requirement lines from the project's requirements.txt."""
    requirements: List[str] = []
    req_file = project_root / "requirements.txt"
    if req_file.exists():
        for line in req_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("-"):
                requirements.append(line)
    return requirements


def collect_requirements(project_root: Path, app) -> List[str]:
    """project requirements.txt plus per-resource dependencies, deduped."""
    requirements = _requirements_txt(project_root)

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


def _canonical_name(name: str) -> str:
    """pep 503 normalized distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _strict_requirement_name(requirement: str) -> str:
    """canonical name of a requirement or Requires-Dist line, or "" when
    the line does not start with a name (url, path, archive filename)."""
    match = _STRICT_REQ_NAME_RE.match(requirement)
    if not match:
        return ""
    if requirement.strip() == match.group(1) and re.search(
        r"\.(whl|zip|tar\.gz|tgz)$", match.group(1), re.I
    ):
        return ""
    return _canonical_name(match.group(1))


def _dist_info_name(dist_info_dir: str) -> str:
    """distribution name from a "name-version.dist-info" directory name."""
    stem = dist_info_dir[: -len(".dist-info")]
    match = re.search(r"-\d", stem)
    return stem[: match.start()] if match else stem


def _read_record(dist_info: Path) -> Optional[set]:
    """paths a wheel installed, relative to the install dir, or None.

    pip --target records scripts and data files relative to its staging
    lib/python dir ("../../bin/torchrun") and then moves them to the top
    of the target, so one leading "../../" maps onto the target.
    """
    record = dist_info / "RECORD"
    if not record.is_file():
        return None
    paths = set()
    with record.open(newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.reader(f):
            if not row:
                continue
            path = posixpath.normpath(row[0].replace("\\", "/"))
            if path.startswith("../../"):
                path = path[len("../../") :]
            if not path.startswith("../") and path not in (".", ".."):
                paths.add(path)
    return paths


def _read_requires(dist_info: Path) -> Optional[set]:
    """canonical names a wheel depends on, or None when unreadable.

    markers and extras are ignored, which can only keep more wheels.
    """
    metadata = dist_info / "METADATA"
    if not metadata.is_file():
        return None
    headers = HeaderParser().parsestr(
        metadata.read_text(encoding="utf-8", errors="replace")
    )
    deps = set()
    for line in headers.get_all("Requires-Dist") or []:
        dep = _strict_requirement_name(line)
        if not dep:
            return None
        deps.add(dep)
    return deps


def installed_closure(env_dir: Path, roots: List[str]) -> set:
    """canonical names of installed distributions the roots pull in,
    roots included. unreadable metadata counts every installed
    distribution as pulled in."""
    requires = {
        _canonical_name(_dist_info_name(d.name)): _read_requires(d)
        for d in env_dir.glob("*.dist-info")
        if d.is_dir()
    }
    names = {_strict_requirement_name(r) for r in roots}
    if "" in names or any(v is None for v in requires.values()):
        return set(requires) | (names - {""})
    seen = set()
    stack = list(names)
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(requires.get(node) or ())
    return seen


def prune_excluded(
    env_dir: Path,
    excluded: List[str],
    roots: List[str],
    image_provided: List[str],
) -> List[str]:
    """remove excluded distributions that pip installed as dependencies.

    split_exclusions only drops excluded packages the user listed. a
    listed package that depends on one (peft -> torch) still installs it,
    and on the worker the vendored copy shadows the image's build. this
    removes every installed excluded distribution, plus the wheels that
    only image_provided packages depend on (torch's nvidia/cuda, sympy,
    networkx), since the image installs those alongside its own torch.

    fails closed: without METADATA and RECORD for every wheel, or with a
    root requirement that has no name (url, path), only the excluded
    distributions themselves are removed. roots and their dependencies
    are never removed unless excluded.

    returns the canonical names of removed distributions.
    """
    dist_infos = {
        _canonical_name(_dist_info_name(d.name)): d
        for d in env_dir.glob("*.dist-info")
        if d.is_dir()
    }
    excluded_set = {_canonical_name(n) for n in excluded}
    targets = set(dist_infos) & excluded_set
    if not targets:
        return []

    records = {name: _read_record(d) for name, d in dist_infos.items()}
    requires = {name: _read_requires(d) for name, d in dist_infos.items()}
    root_names = {_strict_requirement_name(r) for r in roots}
    complete = all(v is not None for v in records.values()) and all(
        v is not None for v in requires.values()
    )

    dropped = set(targets)
    if complete and "" not in root_names:
        provided = {_canonical_name(n) for n in image_provided}
        installed = set(dist_infos)

        def closure(start, stop):
            seen = set()
            stack = [n for n in start if n in installed]
            while stack:
                node = stack.pop()
                if node in seen:
                    continue
                seen.add(node)
                stack.extend(
                    d for d in requires[node] if d in installed and d not in stop
                )
            return seen

        # a user exclusion removes only the named package, so its
        # dependencies are protected like the roots
        pulled = closure(installed & provided, stop=set())
        seed = (installed - pulled) | (installed & root_names)
        seed |= installed & (excluded_set - provided)
        kept = closure(seed - provided, stop=provided)
        dropped |= installed - kept

    kept_files = set()
    dropped_files = set()
    for name, paths in records.items():
        if paths is None:
            continue
        (dropped_files if name in dropped else kept_files).update(paths)
    dropped_files -= kept_files

    root = env_dir.resolve()
    touched_tops = set()
    for rel in dropped_files:
        path = env_dir / rel
        try:
            path.resolve().relative_to(root)
        except ValueError:
            continue
        if path.is_file() or path.is_symlink():
            path.unlink()
            touched_tops.add(rel.split("/", 1)[0])

    for name in dropped:
        shutil.rmtree(dist_infos[name], ignore_errors=True)
        # wheels without a RECORD: fall back to the import-name directory
        if records[name] is None:
            top = env_dir / name.replace("-", "_")
            if top.is_dir() and not top.is_symlink():
                shutil.rmtree(top, ignore_errors=True)

    # drop directories the removal emptied
    for top in touched_tops:
        top_path = env_dir / top
        if not top_path.is_dir() or top_path.is_symlink():
            continue
        for directory in sorted(
            (p for p in top_path.rglob("*") if p.is_dir() and not p.is_symlink()),
            key=lambda p: len(p.parts),
            reverse=True,
        ):
            if not any(directory.iterdir()):
                directory.rmdir()
        if not any(top_path.iterdir()):
            top_path.rmdir()

    return sorted(dropped)


def _is_pinnable_name(spec: str) -> bool:
    """true if the spec is a plain requirement (name, optional pin), as
    opposed to a url, git ref, or filesystem path."""
    return not any(token in spec for token in ("/", "\\", ":", "@")) or bool(
        _REQ_NAME_RE.match(spec)
        and re.match(r"^[A-Za-z0-9._-]+(\[[^\]]+\])?([<>=!~;].*)?$", spec)
    )


def runpod_requirement(scratch_dir: Path) -> str:
    """the runpod sdk spec to vendor into the artifact.

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

    the package is pure python (py3-none-any), so the client's installed
    tree is valid on the worker platform. this pins sdk contracts and
    manifest handling to the client that produced the artifact.
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
    _ensure_pip()
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
        stderr=subprocess.STDOUT,
        text=True,
    )
    count = 0
    output = deque(maxlen=30)
    for line in proc.stdout:
        output.append(line)
        line = line.strip()
        # pip emits one "Collecting <dist>" per resolved distribution
        if line.startswith("Collecting "):
            count += 1
            if progress is not None:
                name = re.split(r"[><=!~\[ (]", line[11:], maxsplit=1)[0]
                progress(count, name)
    proc.wait()
    if proc.returncode != 0:
        raise BuildError(
            f"dependency vendoring failed: {''.join(output)[-3000:]}\n"
            f"packages without linux wheels cannot be vendored; "
            f"exclude them (they must then come from the image)"
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
    # gpu images, and custom images carry no guarantee. builtin cpu
    # images ship without them, but a vendored pypi torch cannot serve
    # them either: built off linux it lacks the nvidia libraries and
    # fails to import, and on linux those libraries exceed the artifact
    # limit. it would still shadow the gpu image's torch, so an app with
    # any builtin gpu resource excludes them app-wide (the artifact is
    # shared), as runpod-flash did
    resources = list(app.resources.values())
    auto_exclude = not any(h.spec.image for h in resources) and any(
        h.spec.gpu for h in resources
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
            [runpod_requirement(scratch)],
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

    # dependencies of vendored packages can pull excluded packages back
    # in (peft -> torch). a vendored torch shadows the image's build on
    # the worker and fails its cuda check, so remove them here
    prune_names = {requirement_name(e) for e in (exclude or [])}
    if auto_exclude:
        prune_names |= SIZE_PROHIBITIVE_PACKAGES
    # workers install manifest-excluded packages their image lacks at
    # cold start. the gpu images have the torch family, so in a mixed app
    # only the cpu resources act on it: record it only if they need it
    cpu_needs = None
    if auto_exclude and any(not h.spec.gpu for h in resources):
        cpu_roots = _requirements_txt(project_root)
        cpu_roots += [
            dep
            for h in resources
            if not h.spec.gpu
            for dep in h.spec.dependencies or []
        ]
        cpu_needs = installed_closure(env_dir, cpu_roots)
    pruned = prune_excluded(
        env_dir,
        sorted(prune_names),
        all_requirements,
        sorted(SIZE_PROHIBITIVE_PACKAGES) if auto_exclude else [],
    )
    if pruned:
        log.info(
            "removed %d dependency packages provided by the worker image "
            "or excluded: %s",
            len(pruned),
            ", ".join(pruned),
        )
        excluded = sorted(
            set(excluded) | ({_canonical_name(n) for n in prune_names} & set(pruned))
        )
    if cpu_needs is not None:
        user_excluded = {_canonical_name(requirement_name(e)) for e in exclude or []}
        excluded = [
            name
            for name in excluded
            if name in user_excluded
            or name not in SIZE_PROHIBITIVE_PACKAGES
            or name in cpu_needs
        ]
        for name in sorted(set(excluded) & SIZE_PROHIBITIVE_PACKAGES):
            log.warning(
                "cpu resources need %s, which is not bundled; their workers "
                "install it at cold start, so give them container disk for it",
                name,
            )

    if not package_spec:
        sync_running_package(env_dir)

    return BuildResult(
        env_dir=env_dir,
        requirements=all_requirements,
        excluded=excluded,
    )
