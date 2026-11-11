"""select project source files using deployment ignore rules."""

import logging
import os
from pathlib import Path
from typing import Iterable, Iterator, List, Tuple

from pathspec import GitIgnoreSpec

log = logging.getLogger(__name__)

DISCOVERY_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".runpod",
    ".flash",
    "build",
    "dist",
}

DEFAULT_IGNORES = [
    ".venv/",
    "venv/",
    "env/",
    "__pycache__/",
    "*.pyc",
    "node_modules/",
    ".DS_Store",
    "*.tar.gz",
    "tests/",
    "test/",
    "test_*.py",
    "*_test.py",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".tox/",
    ".nox/",
]

PROTECTED_IGNORES = GitIgnoreSpec.from_lines(
    [
        ".git",
        ".runpod",
        ".flash",
        "/env",
        "/runpod_manifest.json",
        ".env",
        ".env.*",
        "*.env",
        "*.env.*",
        "*.pem",
        "*.key",
        "id_rsa",
        "id_rsa.*",
        "id_dsa",
        "id_dsa.*",
        "id_ecdsa",
        "id_ecdsa.*",
        "id_ed25519",
        "id_ed25519.*",
        ".ssh",
        ".aws",
        ".azure",
        ".kube",
        ".docker/config.json",
        "**/.docker/config.json",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".git-credentials",
        ".boto",
        "service-account*.json",
        "service_account*.json",
    ]
)


def _load_ignores(path: Path) -> GitIgnoreSpec:
    if path.is_symlink() or not path.is_file():
        return GitIgnoreSpec.from_lines([])
    return GitIgnoreSpec.from_lines(path.read_text(encoding="utf-8").splitlines())


def _is_ignored(rel_path: str, patterns: List[Tuple[str, GitIgnoreSpec]]) -> bool:
    ignored = False
    for prefix, spec in patterns:
        result = spec.check_file(rel_path[len(prefix) :])
        if result.include is not None:
            ignored = result.include
    return ignored


def iter_source_files(
    project_root: Path,
    *,
    excluded_paths: Iterable[Path] = (),
    discovery: bool = False,
) -> Iterator[Path]:
    runpod_ignores = _load_ignores(project_root / ".runpodignore")
    git_ignores = [("", GitIgnoreSpec.from_lines(DEFAULT_IGNORES))]
    excluded = set(excluded_paths)
    for directory, dirs, files in os.walk(project_root, followlinks=False):
        root = Path(directory)
        relative = root.relative_to(project_root).as_posix()
        prefix = "" if relative == "." else relative + "/"
        while not prefix.startswith(git_ignores[-1][0]):
            git_ignores.pop()
        git_ignores.append((prefix, _load_ignores(root / ".gitignore")))
        patterns = [*git_ignores, ("", runpod_ignores)]
        kept_dirs = []
        for name in sorted(dirs):
            path = root / name
            rel = prefix + name + "/"
            if (
                path.is_symlink()
                or path in excluded
                or PROTECTED_IGNORES.match_file(rel)
                or _is_ignored(rel, patterns)
                or (
                    discovery
                    and (
                        name.startswith(".")
                        or name in DISCOVERY_SKIP_DIRS
                        or (path / "pyvenv.cfg").is_file()
                    )
                )
            ):
                if not discovery:
                    log.warning("excluded source path %r from deployment artifact", rel)
                continue
            kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in sorted(files):
            path = root / name
            rel = prefix + name
            if (
                path.is_symlink()
                or not path.is_file()
                or path in excluded
                or PROTECTED_IGNORES.match_file(rel)
                or _is_ignored(rel, patterns)
                or (discovery and (path.suffix != ".py" or name == "setup.py"))
            ):
                if not discovery:
                    log.warning("excluded source path %r from deployment artifact", rel)
                continue
            yield path
