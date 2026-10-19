"""shared command option parsing."""

import click
import pytest

from runpod.rp_cli.options import parse_env


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
