"""rp flash init: project scaffolding."""

import tarfile

from click.testing import CliRunner

from runpod.apps.deploy import package_project
from runpod.apps.init import create_project, detect_conflicts
from runpod.rp_cli.main import cli


class TestCreateProject:
    def test_writes_skeleton(self, tmp_path):
        written = create_project(tmp_path, "my-app")
        names = {p.name for p in written}
        assert names == {"main.py", "requirements.txt", ".runpodignore"}

    def test_existing_files_kept_without_overwrite(self, tmp_path):
        (tmp_path / "main.py").write_text("original")
        written = create_project(tmp_path, "my-app")
        assert (tmp_path / "main.py").read_text() == "original"
        assert tmp_path / "requirements.txt" in written
        assert (tmp_path / "requirements.txt").exists()

    def test_overwrite_replaces_files(self, tmp_path):
        (tmp_path / "main.py").write_text("original")
        create_project(tmp_path, "my-app", overwrite=True)
        assert (tmp_path / "main.py").read_text() != "original"

    def test_creates_directory(self, tmp_path):
        target = tmp_path / "new-project"
        create_project(target, "new-project")
        assert (target / "main.py").exists()

    def test_scaffold_packages_source_without_local_secrets(self, tmp_path):
        create_project(tmp_path, "my-app")
        (tmp_path / ".env.production").write_text("TOKEN=private")
        (tmp_path / "private.pem").write_text("private")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests/test_app.py").write_text("local test")
        (tmp_path / ".gitignore").write_text("local-data/\n")
        (tmp_path / "local-data").mkdir()
        (tmp_path / "local-data/input.json").write_text("{}")

        with tarfile.open(package_project(tmp_path, {})) as tar:
            assert set(tar.getnames()) == {
                "main.py",
                "requirements.txt",
                ".runpodignore",
                ".gitignore",
                "runpod_manifest.json",
            }


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
        result = runner.invoke(cli, ["flash", "init", "demo"])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "demo" / "main.py").exists()

    def test_init_current_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["flash", "init", "."])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "main.py").exists()

    def test_init_conflicts_fail_without_force(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.py").write_text("keep me")
        runner = CliRunner()
        result = runner.invoke(cli, ["flash", "init", "."])
        assert result.exit_code != 0
        assert "main.py" in result.output
        assert (tmp_path / "main.py").read_text() == "keep me"

    def test_init_force_overwrites(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.py").write_text("old")
        runner = CliRunner()
        result = runner.invoke(cli, ["flash", "init", ".", "--force"])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "main.py").read_text() != "old"
