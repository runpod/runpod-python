"""value parsing shared by resource command options."""

import shlex
from collections.abc import Iterable

import click

from runpod.rp_cli.api import list_all
from runpod.rp_cli.compute import ResolutionError


def parse_env(values: Iterable[str]) -> dict[str, str]:
    env = {}
    for value in values:
        key, separator, content = value.partition("=")
        if not key or not separator:
            raise click.UsageError("invalid --env value; use KEY=VALUE")
        env[key] = content
    return env


def container_fields(
    options: dict, current: dict | None = None, unset_env: Iterable[str] = ()
) -> dict:
    current = current or {}
    body = {
        field: options[option]
        for option, field in (
            ("image", "image"),
            ("template", "templateId"),
            ("args", "args"),
            ("disk", "disk"),
            ("global_networking", "globalNetworking"),
        )
        if options.get(option) is not None
    }
    for option in ("cmd", "entrypoint"):
        if options.get(option) is not None:
            try:
                body[option] = shlex.split(options[option])
            except ValueError as exc:
                raise click.BadParameter(str(exc), param_hint=f"--{option}") from exc
    if options.get("ports"):
        body["ports"] = list(options["ports"])
    if options.get("registry") is not None:
        value = options["registry"]
        body["registry"] = None
        if value:
            matches = [
                registry["id"]
                for registry in list_all("/v2/registries", "registries")
                if value in (registry["id"], registry["name"])
            ]
            if not matches:
                raise ResolutionError(
                    f"unknown registry credential '{value}'. list them with rp registry list"
                )
            body["registry"] = matches[0]
    if options.get("env") or unset_env:
        env = {**(current.get("env") or {}), **parse_env(options.get("env") or ())}
        for key in unset_env:
            env.pop(key, None)
        body["env"] = env
    return body
