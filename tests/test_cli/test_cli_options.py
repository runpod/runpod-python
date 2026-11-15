"""shared command option parsing."""

import click
import pytest

from runpod.rp_cli.compute import ResolutionError
from runpod.rp_cli.options import container_fields, parse_env


def test_env_values_preserve_equals_and_allow_empty_values():
    assert parse_env(["TOKEN=a=b", "EMPTY=", "TOKEN=c=d"]) == {
        "TOKEN": "c=d",
        "EMPTY": "",
    }


@pytest.mark.parametrize("value", ["secret-without-equals", "=secret"])
def test_env_error_does_not_echo_the_value(value):
    with pytest.raises(click.UsageError) as exc:
        parse_env([value])
    assert "KEY=VALUE" in str(exc.value)
    assert "secret" not in str(exc.value)


def test_container_fields_preserve_omitted_values():
    assert container_fields({}) == {}
    assert container_fields({"env": (), "ports": (), "registry": None}) == {}


def test_container_fields_map_options_and_parse_commands():
    assert container_fields(
        {
            "image": "image:tag",
            "template": "tpl1",
            "args": "bash start.sh",
            "disk": 0,
            "global_networking": False,
            "cmd": "python -c 'print(1)'",
            "entrypoint": "",
            "ports": ("8000/http", "22/tcp"),
        }
    ) == {
        "image": "image:tag",
        "templateId": "tpl1",
        "args": "bash start.sh",
        "disk": 0,
        "globalNetworking": False,
        "cmd": ["python", "-c", "print(1)"],
        "entrypoint": [],
        "ports": ["8000/http", "22/tcp"],
    }


@pytest.mark.parametrize("option", ["cmd", "entrypoint"])
def test_container_fields_reject_malformed_shell_quoting(option):
    with pytest.raises(click.BadParameter) as exc:
        container_fields({option: "python 'unterminated"})
    assert exc.value.param_hint == f"--{option}"


@pytest.mark.parametrize("registry", ["reg1", "private"])
def test_container_fields_resolve_registry_id_or_name(monkeypatch, registry):
    def list_registries(path, collection):
        assert (path, collection) == ("/v2/registries", "registries")
        return [{"id": "reg1", "name": "private"}]

    monkeypatch.setattr("runpod.rp_cli.options.list_all", list_registries)
    assert container_fields({"registry": registry}) == {"registry": "reg1"}


def test_container_fields_clear_registry_without_lookup(monkeypatch):
    def unexpected_lookup(*args):
        pytest.fail("clearing a registry must not fetch credentials")

    monkeypatch.setattr("runpod.rp_cli.options.list_all", unexpected_lookup)
    assert container_fields({"registry": ""}) == {"registry": None}


def test_container_fields_reject_unknown_registry(monkeypatch):
    monkeypatch.setattr("runpod.rp_cli.options.list_all", lambda *args: [])
    with pytest.raises(ResolutionError, match="unknown registry credential"):
        container_fields({"registry": "missing"})


def test_container_fields_merge_then_unset_environment():
    current = {"env": {"KEEP": "old", "EDIT": "old", "DROP": "old"}}
    assert container_fields(
        {"env": ("EDIT=new", "DROP=new", "EMPTY=")}, current, ("DROP", "MISSING")
    ) == {"env": {"KEEP": "old", "EDIT": "new", "EMPTY": ""}}
    assert current["env"] == {"KEEP": "old", "EDIT": "old", "DROP": "old"}
    assert container_fields({}, {"env": {"DROP": "old"}}, ("DROP",)) == {"env": {}}


@pytest.mark.parametrize("current", [None, {}, {"env": None}])
def test_container_fields_allow_empty_current_environment(current):
    assert container_fields({"env": ("KEY=value",)}, current) == {
        "env": {"KEY": "value"}
    }
