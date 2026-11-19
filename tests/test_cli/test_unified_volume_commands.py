"""volume discovery and id routing across storage backends."""

import json
from unittest.mock import call, patch

import pytest
from click.shell_completion import ShellComplete
from click.testing import CliRunner

from runpod.error import AuthenticationError, QueryError
from runpod.rp_cli.main import cli

NETWORK = {
    "id": "nv1",
    "name": "models",
    "size": 100,
    "dataCenter": "EU-RO-1",
    "type": "HIGH_PERFORMANCE",
}
GLOBAL = {
    "id": "gv1",
    "name": "shared",
    "createdAt": "2026-09-24T00:00:00Z",
    "createdByUserId": "user_example",
}


@pytest.mark.parametrize("command", ["list", "list --json"])
def test_list_includes_every_page_of_both_kinds(command):
    requests = []

    def page(method, path, params):
        requests.append(call(method, path, params=dict(params)))
        assert method == "GET"
        kind = "network" if path == "/v2/network-volumes" else "global"
        record = NETWORK if kind == "network" else GLOBAL
        second_page = bool(params)
        return {
            f"{kind}Volumes": [
                {**record, "id": record["id"] + ("-second" if second_page else "")}
            ],
            "pagination": {
                "hasNextPage": not second_page,
                "nextCursor": None if second_page else "next",
            },
        }

    with patch("runpod.rp_cli.api.run_rest_request", side_effect=page):
        result = CliRunner().invoke(cli, f"volume {command}")
    assert result.exit_code == 0, result.output
    assert requests == [
        call("GET", "/v2/network-volumes", params={}),
        call("GET", "/v2/network-volumes", params={"cursor": "next"}),
        call("GET", "/v2/global-volumes", params={}),
        call("GET", "/v2/global-volumes", params={"cursor": "next"}),
    ]
    if "--json" in command:
        rows = json.loads(result.output)
        assert [row["kind"] for row in rows] == ["network"] * 2 + ["global"] * 2
        assert rows[0]["type"] == "HIGH_PERFORMANCE"
    else:
        for text in ("nv1", "nv1-second", "gv1", "gv1-second", "network", "global"):
            assert text in result.output


def test_global_only_account_lists_without_flag():
    with patch("runpod.rp_cli.volume.list_all", side_effect=[[], [GLOBAL]]):
        result = CliRunner().invoke(cli, ["volume", "list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == [{**GLOBAL, "kind": "global"}]


def test_list_failure_does_not_present_partial_inventory():
    with patch(
        "runpod.rp_cli.volume.list_all",
        side_effect=[[NETWORK], QueryError("global access denied", status_code=403)],
    ):
        result = CliRunner().invoke(cli, ["volume", "list", "--json"])
    assert result.exit_code == 1
    assert "global access denied" in result.output
    assert NETWORK["id"] not in result.output


@pytest.mark.parametrize("failed_kind", ["network", "global"])
@pytest.mark.parametrize("status", [403, 503])
def test_table_lists_available_storage_and_reports_failed_type(failed_kind, status):
    available = GLOBAL if failed_kind == "network" else NETWORK

    def listing(path, collection):
        if path == f"/v2/{failed_kind}-volumes":
            raise QueryError("unavailable", status_code=status)
        return [available]

    with patch("runpod.rp_cli.volume.list_all", side_effect=listing):
        result = CliRunner().invoke(cli, ["volume", "list"])
    assert result.exit_code == 1
    assert available["id"] in result.stdout
    assert f"could not list {failed_kind} volumes" in result.stderr
    assert "incomplete volume inventory" in result.stderr


def test_table_does_not_claim_inventory_is_empty_when_both_backends_fail():
    with patch(
        "runpod.rp_cli.volume.list_all",
        side_effect=QueryError("unavailable", status_code=503),
    ):
        result = CliRunner().invoke(cli, ["volume", "list"])
    assert result.exit_code == 1
    assert "no volumes" not in result.output
    assert "could not list network volumes" in result.stderr
    assert "could not list global volumes" in result.stderr


@pytest.mark.parametrize("resource", ["pod", "endpoint"])
@pytest.mark.parametrize("failed_kind", ["network", "global"])
def test_mount_completion_preserves_available_storage(resource, failed_kind):
    available = GLOBAL if failed_kind == "network" else NETWORK

    def listing(path, collection):
        if path == f"/v2/{failed_kind}-volumes":
            raise QueryError("unavailable", status_code=403)
        return [available]

    with patch("runpod.rp_cli.volume.list_all", side_effect=listing):
        items = ShellComplete(cli, {}, "rp", "_RP_COMPLETE").get_completions(
            [resource, "create", "--volume"], ""
        )
    assert [item.value for item in items] == [available["id"]]


@pytest.mark.parametrize("status", [400, 422])
def test_validation_errors_are_not_treated_as_missing_volumes(status):
    with patch(
        "runpod.rp_cli.volume.run_rest_request",
        side_effect=QueryError("invalid request", status_code=status),
    ) as rest:
        result = CliRunner().invoke(cli, ["volume", "delete", "invalid", "--yes"])
    assert result.exit_code == 1
    rest.assert_called_once_with("GET", "/v2/network-volumes/invalid")


@pytest.mark.parametrize(
    "command,mutation",
    [
        ("get gv1 --json", []),
        (
            "update gv1 --name renamed --json",
            [call("PATCH", "/v2/global-volumes/gv1", json={"name": "renamed"})],
        ),
        ("delete gv1 --yes", [call("DELETE", "/v2/global-volumes/gv1")]),
    ],
)
def test_global_id_routes_without_flag(command, mutation):
    with patch(
        "runpod.rp_cli.volume.run_rest_request",
        side_effect=[
            QueryError("not found", status_code=404),
            GLOBAL,
            GLOBAL,
        ],
    ) as rest:
        result = CliRunner().invoke(cli, f"volume {command}")
    assert result.exit_code == 0, result.output
    assert rest.call_args_list == [
        call("GET", "/v2/network-volumes/gv1"),
        call("GET", "/v2/global-volumes/gv1"),
        *mutation,
    ]
    if "--json" in command:
        assert json.loads(result.output) == GLOBAL
    else:
        assert "deleted global volume" in result.output


@pytest.mark.parametrize("answer,code", [("y", 0), ("n", 1)])
def test_global_deletion_confirmation_without_flag(answer, code):
    with (
        patch(
            "runpod.rp_cli.volume.run_rest_request",
            side_effect=[QueryError("not found", status_code=404), GLOBAL, None],
        ) as rest,
        patch("runpod.rp_cli.output.interactive", return_value=True),
    ):
        result = CliRunner().invoke(
            cli, ["volume", "delete", "gv1"], input=answer + "\n"
        )
    assert result.exit_code == code
    assert "delete global volume gv1 (shared) and all its data?" in result.output
    expected = [
        call("GET", "/v2/network-volumes/gv1"),
        call("GET", "/v2/global-volumes/gv1"),
    ]
    if answer == "y":
        expected.append(call("DELETE", "/v2/global-volumes/gv1"))
    assert rest.call_args_list == expected


@pytest.mark.parametrize("command", ["update gv1 --size 200", "expand gv1 --size +50"])
def test_global_resize_is_rejected_without_mutation(command):
    with patch(
        "runpod.rp_cli.volume.run_rest_request",
        side_effect=[QueryError("not found", status_code=404), GLOBAL],
    ) as rest:
        result = CliRunner().invoke(cli, f"volume {command}")
    assert result.exit_code == 2
    assert "global volumes have no size to change" in result.output
    assert [args.args[0] for args in rest.call_args_list] == ["GET", "GET"]


@pytest.mark.parametrize(
    "command", ["get nv1", "update nv1 --name renamed", "delete nv1 --yes"]
)
@pytest.mark.parametrize(
    "failure",
    [
        AuthenticationError("invalid key"),
        QueryError("forbidden", status_code=403),
        QueryError("unavailable", status_code=503),
    ],
)
def test_lookup_errors_do_not_fall_back_or_mutate(command, failure):
    with patch("runpod.rp_cli.volume.run_rest_request", side_effect=failure) as rest:
        result = CliRunner().invoke(cli, f"volume {command}")
    assert result.exit_code == 1
    assert str(failure) in result.output
    rest.assert_called_once_with("GET", "/v2/network-volumes/nv1")


def test_missing_volume_does_not_mutate_either_backend():
    with patch(
        "runpod.rp_cli.volume.run_rest_request",
        side_effect=QueryError("not found", status_code=404),
    ) as rest:
        result = CliRunner().invoke(cli, ["volume", "delete", "missing", "--yes"])
    assert result.exit_code == 1
    assert rest.call_args_list == [
        call("GET", "/v2/network-volumes/missing"),
        call("GET", "/v2/global-volumes/missing"),
    ]


def test_failed_delete_never_falls_back_to_another_backend():
    with patch(
        "runpod.rp_cli.volume.run_rest_request",
        side_effect=[NETWORK, QueryError("disappeared", status_code=404)],
    ) as rest:
        result = CliRunner().invoke(cli, ["volume", "delete", "nv1", "--yes"])
    assert result.exit_code == 1
    assert rest.call_args_list == [
        call("GET", "/v2/network-volumes/nv1"),
        call("DELETE", "/v2/network-volumes/nv1"),
    ]


def test_expand_completion_offers_only_network_volumes():
    with patch("runpod.rp_cli.volume.list_all", return_value=[NETWORK]) as listing:
        items = ShellComplete(cli, {}, "rp", "_RP_COMPLETE").get_completions(
            ["volume", "expand"], ""
        )
    assert [item.value for item in items] == ["nv1"]
    listing.assert_called_once_with("/v2/network-volumes", "networkVolumes")
