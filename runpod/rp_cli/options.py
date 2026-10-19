"""value parsing shared by resource command options."""

from collections.abc import Iterable

import click


def parse_env(values: Iterable[str]) -> dict[str, str]:
    env = {}
    for value in values:
        key, separator, content = value.partition("=")
        if not key or not separator:
            raise click.UsageError("invalid --env value; use KEY=VALUE")
        env[key] = content
    return env
