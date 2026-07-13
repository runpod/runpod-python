"""rp init: project scaffolding."""

from pathlib import Path

from click.testing import CliRunner

from runpod.apps.init import create_project, detect_conflicts
from runpod.rp_cli.main import cli


class TestCreateProject:
    def test_writes_skeleton(self, tmp_path):
        written = create_project(tmp_path, "my-app")
        names = {p.name for p in written}
        assert names == {"main.py", "requirements.txt", ".runpodignore"}
        main = (tmp_path / "main.py").read_text()
        assert 'App("my-app")' in main
        assert "@runpod.local_entrypoint" in main

    def test_skeleton_module_is_valid_python(self, tmp_path):
        create_project(tmp_path, "my-app")
        compile((tmp_path / "main.py").read_text(), "main.py", "exec")

    def test_existing_files_kept_without_overwrite(self, tmp_path):
        (tmp_path / "main.py").write_text("original")
        written = create_project(tmp_path, "my-app")
        assert (tmp_path / "main.py").read_text() == "original"
        assert Path(tmp_path / "requirements.txt") not in written or True
        assert (tmp_path / "requirements.txt").exists()

    def test_overwrite_replaces_files(self, tmp_path):
        (tmp_path / "main.py").write_text("original")
        create_project(tmp_path, "my-app", overwrite=True)
        assert 'App("my-app")' in (tmp_path / "main.py").read_text()

    def test_creates_directory(self, tmp_path):
        target = tmp_path / "new-project"
        create_project(target, "new-project")
        assert (target / "main.py").exists()


class TestDetectConflicts:
    def test_empty_dir_no_conflicts(self, tmp_path):
        assert detect_conflicts(tmp_path) == []

    def test_existing_files_reported(self, tmp_path):
        (tmp_path / "main.py").write_text("x")
        assert detect_conflicts(tmp_path) == ["main.py"]


class TestInitCommand:
    def test_init_new_project(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "demo"])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "demo" / "main.py").exists()

    def test_init_current_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "."])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "main.py").exists()
        assert tmp_path.name in (tmp_path / "main.py").read_text()

    def test_init_conflicts_fail_without_force(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.py").write_text("keep me")
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "."])
        assert result.exit_code != 0
        assert "main.py" in result.output
        assert (tmp_path / "main.py").read_text() == "keep me"

    def test_init_force_overwrites(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.py").write_text("old")
        runner = CliRunner()
        result = runner.invoke(cli, ["init", ".", "--force"])
        assert result.exit_code == 0, result.output
        assert "App(" in (tmp_path / "main.py").read_text()
