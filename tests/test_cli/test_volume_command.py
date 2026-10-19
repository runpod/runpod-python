"""tests for the rp volume command group."""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from runpod import error
from runpod.rp_cli.main import cli

VOLUME = {
    "id": "vol1",
    "name": "models",
    "size": 100,
    "dataCenter": "EU-RO-1",
    "type": "STANDARD",
}
GLOBAL_VOLUME = {
    "id": "gsb_abc",
    "name": "shared",
    "createdAt": "2026-09-24T00:00:00Z",
    "createdByUserId": "user_123",
}
DATACENTERS = [
    {"id": "EU-RO-1", "region": "EUROPE", "networkVolumeTypes": ["STANDARD"]},
    {"id": "EU-FR-1", "region": "EUROPE", "networkVolumeTypes": ["HIGH_PERFORMANCE"]},
    {
        "id": "EU-CZ-1",
        "region": "EUROPE",
        "networkVolumeTypes": ["STANDARD", "HIGH_PERFORMANCE"],
    },
    {"id": "EU-SE-1", "region": "EUROPE", "networkVolumeTypes": []},
    {"id": "US-TX-3", "region": "NORTH_AMERICA", "networkVolumeTypes": ["STANDARD"]},
]


def invoke(*args):
    return CliRunner().invoke(cli, ["volume", *args])


@pytest.fixture
def rest():
    with patch("runpod.rp_cli.volume.run_rest_request") as mock:
        yield mock


@pytest.fixture
def listing():
    with patch("runpod.rp_cli.compute.list_all") as mock:
        yield mock


@pytest.fixture
def datacenters():
    with patch(
        "runpod.rp_cli.compute.list_datacenters", return_value=DATACENTERS
    ) as mock:
        yield mock


def test_volume_is_mounted():
    assert "volume" in cli.commands


class TestList:
    def test_network_table(self, listing):
        listing.return_value = [VOLUME]
        result = invoke("list")
        assert result.exit_code == 0, result.output
        listing.assert_called_once_with("/v2/network-volumes", "networkVolumes")
        for text in (
            "ID",
            "DATA CENTER",
            "TYPE",
            "vol1",
            "models",
            "100 GB",
            "EU-RO-1",
            "standard",
        ):
            assert text in result.output

    def test_global_json(self, listing):
        listing.return_value = [GLOBAL_VOLUME]
        result = invoke("list", "--global", "--json")
        assert result.exit_code == 0, result.output
        listing.assert_called_once_with("/v2/global-volumes", "globalVolumes")
        assert json.loads(result.output) == [GLOBAL_VOLUME]

    def test_global_table(self, listing):
        listing.return_value = [GLOBAL_VOLUME]
        result = invoke("list", "--global")
        assert "gsb_abc" in result.output and "user_123" in result.output

    def test_empty(self, listing):
        listing.return_value = []
        result = invoke("list")
        assert result.exit_code == 0
        assert "no network volumes" in result.output

    def test_api_error(self, listing):
        listing.side_effect = error.AuthenticationError("bad key")
        result = invoke("list")
        assert result.exit_code == 1
        assert "bad key" in result.output


class TestGet:
    def test_fields(self, rest):
        rest.return_value = VOLUME
        result = invoke("get", "vol1")
        assert result.exit_code == 0, result.output
        rest.assert_called_once_with("GET", "/v2/network-volumes/vol1")
        assert "data center" in result.output and "EU-RO-1" in result.output
        assert "standard" in result.output

    def test_global_json(self, rest):
        rest.return_value = GLOBAL_VOLUME
        result = invoke("get", "gsb_abc", "--global", "--json")
        rest.assert_called_once_with("GET", "/v2/global-volumes/gsb_abc")
        assert json.loads(result.output) == GLOBAL_VOLUME

    def test_picker(self, rest, listing):
        listing.return_value = [VOLUME]
        rest.return_value = VOLUME
        with patch("runpod.rp_cli.volume.pick", return_value="vol1") as pick:
            result = invoke("get")
        assert result.exit_code == 0, result.output
        choices = pick.call_args.args[1]
        assert choices == [("vol1", "models  100 GB  EU-RO-1  standard")]
        rest.assert_called_once_with("GET", "/v2/network-volumes/vol1")

    def test_missing_id_without_tty(self, rest, listing):
        listing.return_value = [VOLUME]
        result = invoke("get")
        assert result.exit_code == 2
        assert "pass VOLUME_ID" in result.output
        rest.assert_not_called()

    def test_not_found(self, rest):
        rest.side_effect = error.QueryError("network volume not found", status_code=404)
        result = invoke("get", "nope")
        assert result.exit_code == 1
        assert "not found" in result.output


class TestCreate:
    def test_exact_data_center(self, rest, datacenters):
        rest.return_value = VOLUME
        result = invoke(
            "create",
            "--name",
            "models",
            "--size",
            "100",
            "--data-center",
            "EU-RO-1",
            "--type",
            "standard",
        )
        assert result.exit_code == 0, result.output
        datacenters.assert_not_called()
        rest.assert_called_once_with(
            "POST",
            "/v2/network-volumes",
            json={
                "name": "models",
                "size": 100,
                "dataCenter": "EU-RO-1",
                "type": "STANDARD",
            },
        )
        assert "created network volume" in result.output and "vol1" in result.output

    def test_region_narrows_by_type(self, rest, datacenters):
        rest.return_value = VOLUME
        with patch("runpod.rp_cli.volume.pick", return_value="EU-CZ-1") as pick:
            result = invoke(
                "create",
                "--name",
                "m",
                "--size",
                "10",
                "--region",
                "eu",
                "--type",
                "high_performance",
                "--json",
            )
        assert result.exit_code == 0, result.output
        assert [value for value, _ in pick.call_args.args[1]] == ["EU-FR-1", "EU-CZ-1"]
        assert rest.call_args.kwargs["json"] == {
            "name": "m",
            "size": 10,
            "dataCenter": "EU-CZ-1",
            "type": "HIGH_PERFORMANCE",
        }
        assert json.loads(result.output) == VOLUME

    def test_region_with_one_candidate(self, rest, datacenters):
        rest.return_value = VOLUME
        result = invoke("create", "--name", "m", "--size", "10", "--region", "na")
        assert result.exit_code == 0, result.output
        assert rest.call_args.kwargs["json"] == {
            "name": "m",
            "size": 10,
            "dataCenter": "US-TX-3",
        }

    def test_ambiguous_region_without_tty_lists_candidates(self, rest, datacenters):
        result = invoke("create", "--name", "m", "--size", "10", "--region", "eu")
        assert result.exit_code == 2
        assert "EU-RO-1, EU-FR-1, EU-CZ-1" in result.output
        assert "EU-SE-1" not in result.output
        assert "--data-center" in result.output
        rest.assert_not_called()

    def test_no_region_picks_from_every_data_center(self, rest, datacenters):
        rest.return_value = VOLUME
        with patch("runpod.rp_cli.volume.pick", return_value="US-TX-3") as pick:
            result = invoke(
                "create", "--name", "m", "--size", "10", "--type", "standard"
            )
        assert result.exit_code == 0, result.output
        labels = dict(pick.call_args.args[1])
        assert set(labels) == {"EU-RO-1", "EU-CZ-1", "US-TX-3"}
        assert labels["EU-CZ-1"] == "EU-CZ-1  europe  standard, high_performance"

    def test_region_without_type_support(self, rest, datacenters):
        result = invoke(
            "create",
            "--name",
            "m",
            "--size",
            "10",
            "--region",
            "na",
            "--type",
            "high_performance",
        )
        assert result.exit_code == 1
        assert "no data center in region na offers high_performance" in result.output
        rest.assert_not_called()

    def test_unknown_region(self, rest, datacenters):
        result = invoke("create", "--name", "m", "--size", "10", "--region", "mars")
        assert result.exit_code == 1
        assert "unknown region 'mars'" in result.output

    def test_requires_size(self, rest):
        result = invoke("create", "--name", "m", "--data-center", "EU-RO-1")
        assert result.exit_code == 2
        assert "--size" in result.output
        rest.assert_not_called()

    def test_size_bounds(self, rest):
        result = invoke(
            "create", "--name", "m", "--size", "5000", "--data-center", "EU-RO-1"
        )
        assert result.exit_code == 2
        rest.assert_not_called()

    def test_region_and_data_center_conflict(self, rest):
        result = invoke(
            "create",
            "--name",
            "m",
            "--size",
            "10",
            "--region",
            "eu",
            "--data-center",
            "EU-RO-1",
        )
        assert result.exit_code == 2
        assert "either --region or --data-center" in result.output

    def test_global(self, rest):
        rest.return_value = GLOBAL_VOLUME
        result = invoke("create", "--name", "shared", "--global")
        assert result.exit_code == 0, result.output
        rest.assert_called_once_with(
            "POST", "/v2/global-volumes", json={"name": "shared"}
        )
        assert "created global volume" in result.output and "gsb_abc" in result.output

    def test_global_rejects_network_flags(self, rest):
        result = invoke(
            "create", "--name", "s", "--global", "--size", "10", "--region", "eu"
        )
        assert result.exit_code == 2
        assert "--size, --region do not apply to global volumes" in result.output
        rest.assert_not_called()


class TestUpdate:
    def test_rename(self, rest):
        rest.return_value = dict(VOLUME, name="renamed")
        result = invoke("update", "vol1", "--name", "renamed")
        assert result.exit_code == 0, result.output
        rest.assert_called_once_with(
            "PATCH", "/v2/network-volumes/vol1", json={"name": "renamed"}
        )
        assert "renamed" in result.output

    def test_rename_and_resize(self, rest):
        rest.side_effect = [VOLUME, dict(VOLUME, name="r", size=200)]
        result = invoke("update", "vol1", "--name", "r", "--size", "200", "--json")
        assert result.exit_code == 0, result.output
        assert rest.call_args.args == ("PATCH", "/v2/network-volumes/vol1")
        assert rest.call_args.kwargs["json"] == {"name": "r", "size": 200}
        assert json.loads(result.output)["size"] == 200

    def test_same_size_is_allowed(self, rest):
        rest.side_effect = [VOLUME, VOLUME]
        result = invoke("update", "vol1", "--size", "100")
        assert result.exit_code == 0, result.output

    def test_rejects_shrinking(self, rest):
        rest.return_value = VOLUME
        result = invoke("update", "vol1", "--size", "50")
        assert result.exit_code == 2
        assert "can only grow: vol1 is 100 GB, requested 50 GB" in result.output
        rest.assert_called_once_with("GET", "/v2/network-volumes/vol1")

    def test_requires_a_change(self, rest):
        result = invoke("update", "vol1")
        assert result.exit_code == 2
        assert "--name and/or --size" in result.output

    def test_global_rename(self, rest):
        rest.return_value = dict(GLOBAL_VOLUME, name="renamed")
        result = invoke("update", "gsb_abc", "--global", "--name", "renamed")
        assert result.exit_code == 0, result.output
        rest.assert_called_once_with(
            "PATCH", "/v2/global-volumes/gsb_abc", json={"name": "renamed"}
        )

    def test_global_rejects_size(self, rest):
        result = invoke("update", "gsb_abc", "--global", "--size", "20")
        assert result.exit_code == 2
        assert "global volumes have no size" in result.output
        rest.assert_not_called()


class TestExpand:
    def test_absolute(self, rest):
        rest.side_effect = [VOLUME, dict(VOLUME, size=250)]
        result = invoke("expand", "vol1", "--size", "250")
        assert result.exit_code == 0, result.output
        rest.assert_called_with("PATCH", "/v2/network-volumes/vol1", json={"size": 250})
        assert "from 100 GB to 250 GB" in result.output

    def test_relative(self, rest):
        rest.side_effect = [VOLUME, dict(VOLUME, size=150)]
        result = invoke("expand", "vol1", "--size", "+50", "--json")
        assert result.exit_code == 0, result.output
        rest.assert_called_with("PATCH", "/v2/network-volumes/vol1", json={"size": 150})
        assert json.loads(result.output)["size"] == 150

    @pytest.mark.parametrize("size", ["50", "100", "+0"])
    def test_rejects_shrinking_or_no_growth(self, rest, size):
        rest.return_value = VOLUME
        result = invoke("expand", "vol1", "--size", size)
        assert result.exit_code == 2
        assert "network volumes can only grow: vol1 is 100 GB" in result.output
        assert rest.call_count == 1

    def test_rejects_past_maximum(self, rest):
        rest.return_value = VOLUME
        result = invoke("expand", "vol1", "--size", "+4000")
        assert result.exit_code == 2
        assert "at most 4096 GB, requested 4100 GB" in result.output
        assert rest.call_count == 1

    @pytest.mark.parametrize("size", ["abc", "-5", "1.5"])
    def test_rejects_malformed_size(self, rest, size):
        result = invoke("expand", "vol1", "--size", size)
        assert result.exit_code == 2
        assert "is not a size" in result.output
        rest.assert_not_called()


class TestDelete:
    def test_yes_skips_confirmation(self, rest):
        result = invoke("delete", "vol1", "--yes")
        assert result.exit_code == 0, result.output
        rest.assert_called_once_with("DELETE", "/v2/network-volumes/vol1")
        assert "deleted network volume" in result.output

    def test_confirms_interactively(self, rest):
        rest.return_value = VOLUME
        with patch("runpod.rp_cli.output.interactive", return_value=True):
            result = CliRunner().invoke(cli, ["volume", "delete", "vol1"], input="y\n")
        assert result.exit_code == 0, result.output
        assert "models  100 GB  EU-RO-1  standard" in result.output
        rest.assert_called_with("DELETE", "/v2/network-volumes/vol1")

    def test_declined(self, rest):
        rest.return_value = VOLUME
        with patch("runpod.rp_cli.output.interactive", return_value=True):
            result = CliRunner().invoke(cli, ["volume", "delete", "vol1"], input="n\n")
        assert result.exit_code == 1
        assert all(call.args[0] == "GET" for call in rest.call_args_list)

    def test_requires_yes_without_tty(self, rest):
        rest.return_value = VOLUME
        result = invoke("delete", "vol1")
        assert result.exit_code == 2
        assert "--yes" in result.output
        assert all(call.args[0] == "GET" for call in rest.call_args_list)

    def test_global(self, rest):
        result = invoke("delete", "gsb_abc", "--global", "--yes")
        assert result.exit_code == 0, result.output
        rest.assert_called_once_with("DELETE", "/v2/global-volumes/gsb_abc")

    def test_mounted_global_volume(self, rest):
        rest.side_effect = error.QueryError("volume is still mounted", status_code=409)
        result = invoke("delete", "gsb_abc", "--global", "--yes")
        assert result.exit_code == 1
        assert "still mounted" in result.output


class TestCompletion:
    def _complete(self, args, incomplete):
        from click.shell_completion import ShellComplete

        return [
            item.value
            for item in ShellComplete(cli, {}, "rp", "_RP_COMPLETE").get_completions(
                args, incomplete
            )
        ]

    def test_network_ids(self, listing):
        listing.return_value = [VOLUME, dict(VOLUME, id="other")]
        assert self._complete(["volume", "get"], "v") == ["vol1"]
        listing.assert_called_with("/v2/network-volumes", "networkVolumes")

    def test_global_ids(self, listing):
        listing.return_value = [GLOBAL_VOLUME]
        assert self._complete(["volume", "delete", "--global"], "") == ["gsb_abc"]
        listing.assert_called_with("/v2/global-volumes", "globalVolumes")

    def test_errors_complete_nothing(self, listing):
        listing.side_effect = error.AuthenticationError("no key")
        assert self._complete(["volume", "update"], "") == []
