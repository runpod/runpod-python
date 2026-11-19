"""app deletion makes retained storage billing explicit."""

from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from runpod.apps.manage import UndeployResult
from runpod.rp_cli.main import cli


@pytest.mark.parametrize(
    "args,answer,deleted",
    [([], "y\n", True), ([], "n\n", False), (["--yes"], None, True)],
)
def test_app_delete_warns_before_confirmation(args, answer, deleted):
    delete = AsyncMock(return_value=UndeployResult(app_deleted=True))
    with patch("runpod.apps.manage.delete_app", delete):
        result = CliRunner().invoke(
            cli, ["flash", "app", "delete", "demo", *args], input=answer
        )
    assert result.exit_code == (0 if deleted else 1)
    output = " ".join(result.output.split())
    assert "retains network and global volumes" in output
    assert "continue billing" in output
    assert "rp volume list" in output
    assert "rp volume delete VOLUME_ID" in output
    assert "--global" not in output
    if args:
        assert "and all its environments?" not in output
    else:
        assert output.index("continue billing") < output.index(
            "and all its environments?"
        )
    if deleted:
        delete.assert_awaited_once()
    else:
        delete.assert_not_awaited()


def test_app_delete_help_explains_storage_retention():
    result = CliRunner().invoke(cli, ["flash", "app", "delete", "--help"])
    assert result.exit_code == 0
    assert "Volumes are retained and keep billing." in " ".join(result.output.split())
