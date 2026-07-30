"""tests for the dev-session file watcher."""

import time

from runpod.apps.watch import FileWatcher


def test_no_change_initially(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    watcher = FileWatcher([tmp_path])
    assert watcher.changed() is False


def test_detects_modification(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1")
    watcher = FileWatcher([tmp_path])
    time.sleep(0.01)
    f.write_text("x = 2")
    # mtime granularity can be coarse; force it
    import os

    os.utime(f, (time.time() + 1, time.time() + 1))
    assert watcher.changed() is True
    assert watcher.changed() is False


def test_detects_new_file(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    watcher = FileWatcher([tmp_path])
    (tmp_path / "b.py").write_text("y = 2")
    assert watcher.changed() is True


def test_detects_deleted_file(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1")
    watcher = FileWatcher([tmp_path])
    f.unlink()
    assert watcher.changed() is True


def test_ignores_non_python(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    watcher = FileWatcher([tmp_path])
    (tmp_path / "notes.txt").write_text("hi")
    assert watcher.changed() is False


def test_ignores_skip_dirs(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    watcher = FileWatcher([tmp_path])
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pkg.py").write_text("z = 3")
    assert watcher.changed() is False


def test_wait_for_change_timeout(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    watcher = FileWatcher([tmp_path])
    assert watcher.wait_for_change(poll_interval=0.05, timeout=0.2) is False
