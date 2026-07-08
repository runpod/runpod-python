"""project scaffolding for `rp init`.

writes a minimal app project: a main module with one queue function and
a local entrypoint, a requirements file, and a .runpodignore. existing
files are never overwritten unless the caller says so.
"""

from pathlib import Path
from typing import Dict, List

MAIN_TEMPLATE = '''"""{name}: a Runpod app.

Run it live:

    rp dev main.py

Deploy it:

    rp deploy
"""

import runpod
from runpod import App

app = App("{name}")


@app.queue(cpu="cpu3c-1-2")
def hello(name: str):
    # this print streams back to your terminal during rp dev
    print(f"running in the cloud, greeting {{name}}")
    return f"hello {{name}}!"


@runpod.local_entrypoint
def main():
    # runs on your machine; hello() runs on a cloud worker
    print(hello.remote("world"))
'''

REQUIREMENTS_TEMPLATE = """# packages your functions need on the workers
# (also installed locally for rp dev)
"""

RUNPODIGNORE_TEMPLATE = """# excluded from the deploy artifact
.git
.venv
__pycache__
*.pyc
.env
"""

PROJECT_FILES: Dict[str, str] = {
    "main.py": MAIN_TEMPLATE,
    "requirements.txt": REQUIREMENTS_TEMPLATE,
    ".runpodignore": RUNPODIGNORE_TEMPLATE,
}


def detect_conflicts(project_dir: Path) -> List[str]:
    """names of skeleton files that already exist in project_dir."""
    return [
        name for name in PROJECT_FILES if (project_dir / name).exists()
    ]


def create_project(
    project_dir: Path, name: str, *, overwrite: bool = False
) -> List[Path]:
    """write the project skeleton; returns the files written."""
    project_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    for filename, template in PROJECT_FILES.items():
        path = project_dir / filename
        if path.exists() and not overwrite:
            continue
        content = template.format(name=name) if "{name}" in template else template
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written
