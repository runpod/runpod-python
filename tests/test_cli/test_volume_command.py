"""tests for the rp volume command group."""

import json
from unittest.mock import call, patch

import pytest
from click.shell_completion import ShellComplete
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
    "id": "cmvfsn771000007jp15tn8esm",
    "name": "capy-test-global-renamed",
    "createdAt": "2026-09-24T00:00:00Z",
    "createdByUserId": "user_example",
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


def invoke(command, **kwargs):
    return CliRunner().invoke(cli, f"volume {command}", **kwargs)


@pytest.fixture
def rest():
    with patch("runpod.rp_cli.volume.run_rest_request", return_value=VOLUME) as mock:
        yield mock


@pytest.fixture
def listing():
    with patch("runpod.rp_cli.volume.list_all", return_value=[VOLUME]) as mock:
        yield mock


@pytest.fixture
def datacenters():
    with patch(
        "runpod.rp_cli.compute.list_datacenters", return_value=DATACENTERS
    ) as mock:
        yield mock


@pytest.fixture(params=["network", "global"])
def resource(request, rest):
    kind = request.param
    record = GLOBAL_VOLUME if kind == "global" else VOLUME
    rest.return_value = record
    return kind, record, "--global" if kind == "global" else ""


def test_list_table_and_json(resource, listing):
    kind, record, flag = resource
    listing.return_value = [record]
    result = invoke(f"list {flag}")
    assert result.exit_code == 0, result.output
    assert record["id"] in result.output
    if kind == "global":
        assert "2026-09-24" in result.output
        assert record["createdByUserId"] not in result.output
    else:
        for text in ("ID", "DATA CENTER", "TYPE", "100 GB", "EU-RO-1", "standard"):
            assert text in result.output
    result = invoke(f"list {flag} --json")
    assert json.loads(result.output) == [record]
    listing.assert_called_with(f"/v2/{kind}-volumes", f"{kind}Volumes")


def test_list_empty(listing):
    listing.return_value = []
    assert "no network volumes" in invoke("list").output


def test_get_fields_and_json(resource, rest):
    kind, record, flag = resource
    result = invoke(f"get {record['id']} {flag}")
    assert result.exit_code == 0, result.output
    for value in record.values():
        assert str(value).lower() in result.output.lower()
    result = invoke(f"get {record['id']} {flag} --json")
    assert json.loads(result.output) == record
    rest.assert_called_with("GET", f"/v2/{kind}-volumes/{record['id']}")


def test_get_picker(rest, listing):
    with patch("runpod.rp_cli.volume.pick", return_value="vol1") as pick:
        result = invoke("get")
    assert result.exit_code == 0, result.output
    assert pick.call_args.args[1] == [("vol1", "models  100 GB  EU-RO-1  standard")]
    rest.assert_called_once_with("GET", "/v2/network-volumes/vol1")


def test_missing_id_requires_terminal(rest, listing):
    result = invoke("get")
    assert result.exit_code == 2
    assert "pass VOLUME_ID" in result.output
    rest.assert_not_called()


@pytest.mark.parametrize(
    "command,exception",
    [
        ("get vol1", error.QueryError("not found", status_code=404)),
        ("get vol1", error.AuthenticationError("bad key")),
        (
            "delete vol1 --global --yes",
            error.QueryError("still mounted", status_code=409),
        ),
    ],
)
def test_api_errors_are_clean(rest, command, exception):
    rest.side_effect = exception
    result = invoke(command)
    assert result.exit_code == 1
    assert str(exception) in result.output


def test_create_exact_data_center(rest, datacenters):
    result = invoke(
        "create --name models --size 100 --data-center EU-RO-1 --type standard"
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
    assert "created network volume" in result.output


@pytest.mark.parametrize(
    "options,candidates",
    [
        ("--region eu --type high_performance", ["EU-FR-1", "EU-CZ-1"]),
        ("--type standard", ["EU-RO-1", "EU-CZ-1", "US-TX-3"]),
        ("--region eu --region na", ["EU-RO-1", "EU-FR-1", "EU-CZ-1", "US-TX-3"]),
    ],
)
def test_create_filters_picker_candidates(rest, datacenters, options, candidates):
    with patch("runpod.rp_cli.volume.pick", return_value=candidates[0]) as pick:
        result = invoke(f"create --name m --size 10 {options} --json")
    assert result.exit_code == 0, result.output
    assert [value for value, _ in pick.call_args.args[1]] == candidates
    assert rest.call_args.kwargs["json"]["dataCenter"] == candidates[0]
    assert json.loads(result.output) == VOLUME


def test_create_single_region_match_uses_default_tier(rest, datacenters):
    result = invoke("create --name m --size 10 --region na")
    assert result.exit_code == 0, result.output
    rest.assert_called_once_with(
        "POST",
        "/v2/network-volumes",
        json={
            "name": "m",
            "size": 10,
            "dataCenter": "US-TX-3",
        },
    )


@pytest.mark.parametrize(
    "options,code,message",
    [
        ("--region eu", 2, "EU-RO-1, EU-FR-1, EU-CZ-1"),
        ("--region na --type high_performance", 1, "no data center"),
        ("--region mars", 1, "unknown region"),
        ("--region eu --data-center EU-RO-1", 2, "either --region or --data-center"),
        ("--size 5000", 2, "4096"),
    ],
)
def test_create_rejects_invalid_placement_and_size(
    rest, datacenters, options, code, message
):
    result = invoke(f"create --name m --size 10 {options}")
    assert result.exit_code == code
    assert message in result.output
    rest.assert_not_called()


def test_create_requires_network_size(rest):
    result = invoke("create --name m --data-center EU-RO-1")
    assert result.exit_code == 2
    assert "pass --size" in result.output
    rest.assert_not_called()


def test_create_global(rest):
    rest.return_value = GLOBAL_VOLUME
    result = invoke("create --name shared --global")
    assert result.exit_code == 0, result.output
    rest.assert_called_once_with("POST", "/v2/global-volumes", json={"name": "shared"})


@pytest.mark.parametrize(
    "command,message",
    [
        ("create --name s --global --size 10 --region eu", "do not apply"),
        ("update vol1 --global --size 20", "global volumes have no size"),
        ("update vol1", "pass --name and/or --size"),
    ],
)
def test_invalid_updates_and_global_options(rest, command, message):
    result = invoke(command)
    assert result.exit_code == 2
    assert message in result.output
    rest.assert_not_called()


def test_rename_without_fetching(resource, rest):
    kind, record, flag = resource
    result = invoke(f"update {record['id']} {flag} --name renamed")
    assert result.exit_code == 0, result.output
    rest.assert_called_once_with(
        "PATCH", f"/v2/{kind}-volumes/{record['id']}", json={"name": "renamed"}
    )


@pytest.mark.parametrize(
    "command,target",
    [
        ("update vol1 --size 200 --name renamed", 200),
        ("update vol1 --size 100", 100),
        ("expand vol1 --size 250", 250),
        ("expand vol1 --size +50", 150),
    ],
)
def test_resize_requests(rest, command, target):
    updated = dict(VOLUME, size=target)
    rest.side_effect = [VOLUME, updated]
    result = invoke(f"{command} --json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == updated
    body = {"size": target}
    if "--name" in command:
        body["name"] = "renamed"
    assert rest.call_args_list == [
        call("GET", "/v2/network-volumes/vol1"),
        call("PATCH", "/v2/network-volumes/vol1", json=body),
    ]


def test_expand_human_output(rest):
    rest.side_effect = [VOLUME, dict(VOLUME, size=101)]
    result = invoke("expand vol1 --size +1")
    assert result.exit_code == 0, result.output
    assert "from 100 GB to 101 GB" in result.output


@pytest.mark.parametrize(
    "command,message",
    [
        ("update vol1 --size 50", "can only grow"),
        ("expand vol1 --size 50", "can only grow"),
        ("expand vol1 --size +0", "can only grow"),
        ("expand vol1 --size +4000", "at most 4096 GB"),
    ],
)
def test_resize_rejects_shrink_no_growth_and_overflow(rest, command, message):
    result = invoke(command)
    assert result.exit_code == 2
    assert message in result.output
    rest.assert_called_once_with("GET", "/v2/network-volumes/vol1")


@pytest.mark.parametrize("size", ["abc", "-5", "1.5"])
def test_expand_rejects_malformed_size_without_fetching(rest, size):
    result = invoke(f"expand vol1 --size {size}")
    assert result.exit_code == 2
    assert "is not a size" in result.output
    rest.assert_not_called()


def test_delete_yes(resource, rest):
    kind, record, flag = resource
    result = invoke(f"delete {record['id']} {flag} --yes")
    assert result.exit_code == 0, result.output
    rest.assert_called_once_with("DELETE", f"/v2/{kind}-volumes/{record['id']}")


@pytest.mark.parametrize("answer,code", [("y", 0), ("n", 1)])
def test_delete_confirmation(rest, answer, code):
    with patch("runpod.rp_cli.output.interactive", return_value=True):
        result = invoke("delete vol1", input=f"{answer}\n")
    assert result.exit_code == code
    assert "models  100 GB  EU-RO-1  standard" in result.output
    expected = [call("GET", "/v2/network-volumes/vol1")]
    if answer == "y":
        expected.append(call("DELETE", "/v2/network-volumes/vol1"))
    assert rest.call_args_list == expected


def test_delete_requires_yes_without_terminal(rest):
    result = invoke("delete vol1")
    assert result.exit_code == 2
    assert "pass --yes" in result.output
    rest.assert_called_once_with("GET", "/v2/network-volumes/vol1")


def test_completion_by_kind(resource, listing):
    kind, record, flag = resource
    listing.return_value = [record, dict(record, id="other")]
    args = ["volume", "get"] + ([flag] if flag else [])
    items = ShellComplete(cli, {}, "rp", "_RP_COMPLETE").get_completions(
        args, record["id"][:1]
    )
    assert [item.value for item in items] == [record["id"]]
    listing.assert_called_once_with(f"/v2/{kind}-volumes", f"{kind}Volumes")


def test_completion_suppresses_errors(listing):
    listing.side_effect = error.AuthenticationError("no key")
    items = ShellComplete(cli, {}, "rp", "_RP_COMPLETE").get_completions(
        ["volume", "expand"], ""
    )
    assert items == []
