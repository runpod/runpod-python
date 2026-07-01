"""the `rp` cli: deploy, dev, login, plus mounted legacy groups.

typer is the root; the existing click groups from runpod.cli mount
underneath so `rp pod ...`, `rp ssh ...`, etc keep working.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    name="rp",
    help="Runpod CLI: deploy and manage apps, endpoints, and pods.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


def _echo(message: str) -> None:
    typer.echo(message)


def _fail(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


@app.command()
def deploy(
    target: Optional[Path] = typer.Argument(
        None,
        help="Directory or file to deploy (defaults to current directory).",
    ),
    env: Optional[str] = typer.Option(
        None, "--env", "-e", help="Target environment name."
    ),
):
    """package and deploy all apps found in TARGET."""
    from runpod.apps.deploy import deploy_app
    from runpod.apps.discovery import DiscoveryError, discover_apps

    target = (target or Path.cwd()).resolve()
    if not target.exists():
        _fail(f"{target} does not exist")

    project_root = target if target.is_dir() else target.parent

    _echo(f"discovering apps in {target} ...")
    try:
        apps = discover_apps(target)
    except DiscoveryError as exc:
        _fail(str(exc))

    if not apps:
        _fail(
            "no runpod.App found. define one with:\n\n"
            "    from runpod import App\n"
            '    app = App("my-app")\n'
        )

    for found in apps:
        names = ", ".join(found.resources) or "(no resources)"
        _echo(f"deploying app '{found.name}': {names}")
        result = asyncio.run(
            deploy_app(found, project_root, env_name=env)
        )
        _echo(
            f"  build {result.build_id} active on "
            f"{result.app_name}/{env or found.env}"
        )
    _echo("done.")


@app.command()
def dev(
    module: Path = typer.Argument(..., help="Python module to run."),
):
    """start an interactive dev session for MODULE.

    provisions temporary live endpoints, runs the module's
    @runpod.local_entrypoint, watches for file changes (re-scanning and
    refreshing endpoints so requests run fresh code), and deletes the
    endpoints on exit.
    """
    from runpod.apps.dev import DevSession
    from runpod.apps.discovery import DiscoveryError, discover_apps
    from runpod.apps.entrypoint import get_entrypoint, run_entrypoint
    from runpod.apps.watch import FileWatcher

    module = module.resolve()
    if not module.is_file():
        _fail(f"{module} is not a file")

    os.environ["RUNPOD_DEV_SESSION"] = "1"

    def _scan():
        try:
            apps = discover_apps(module)
        except DiscoveryError as exc:
            _fail(str(exc))
        if not apps:
            _fail("no runpod.App found in module")
        entrypoint = get_entrypoint()
        if entrypoint is None:
            _fail(
                "no @runpod.local_entrypoint found. define one:\n\n"
                "    @runpod.local_entrypoint\n"
                "    async def main(): ...\n"
            )
        return apps, entrypoint

    apps, entrypoint = _scan()

    async def _wait_for_rerun(watcher: FileWatcher) -> str:
        """race the enter key against a file change."""
        loop = asyncio.get_event_loop()
        stdin_task = loop.run_in_executor(None, sys.stdin.readline)
        try:
            while True:
                if watcher.changed():
                    return "changed"
                done, _ = await asyncio.wait({stdin_task}, timeout=0.5)
                if done:
                    return "enter"
        finally:
            stdin_task.cancel()

    async def _session() -> None:
        nonlocal apps, entrypoint
        session = DevSession(apps)
        watcher = FileWatcher([module.parent])
        _echo("provisioning dev endpoints ...")
        await session.start()
        try:
            while True:
                _echo("--- running entrypoint ---")
                try:
                    run_entrypoint(entrypoint)
                except Exception as exc:  # noqa: BLE001 - dev loop survives user errors
                    typer.secho(f"entrypoint error: {exc}", fg=typer.colors.RED)
                _echo("--- press enter to re-run, edit files to reload, ctrl-c to exit ---")
                reason = await _wait_for_rerun(watcher)
                if reason == "changed":
                    _echo("--- change detected: re-scanning and refreshing endpoints ---")
                    apps, entrypoint = _scan()
                    await session.refresh(apps)
        finally:
            _echo("cleaning up dev endpoints ...")
            await session.stop()

    try:
        asyncio.run(_session())
    except (KeyboardInterrupt, EOFError):
        _echo("\nsession ended.")


@app.command()
def login():
    """authenticate with runpod and store the api key."""
    from runpod.cli.groups.config.functions import set_credentials

    api_key = typer.prompt("Runpod API key", hide_input=True)
    try:
        set_credentials(api_key, overwrite=True)
    except ValueError as exc:
        _fail(str(exc))
    _echo("credentials saved to ~/.runpod/config.toml")


def _mount_legacy_groups() -> None:
    """mount the existing click groups so `rp pod ...` etc work."""
    import typer.main as typer_main

    from runpod.cli.groups.config.commands import config_wizard
    from runpod.cli.groups.exec.commands import exec_cli
    from runpod.cli.groups.pod.commands import pod_cli
    from runpod.cli.groups.ssh.commands import ssh_cli

    click_app = typer_main.get_command(app)
    for command in (config_wizard, ssh_cli, pod_cli, exec_cli):
        click_app.add_command(command)
    globals()["cli"] = click_app


_mount_legacy_groups()


def run() -> None:
    """console-script entry point."""
    cli()  # noqa: F821 - bound by _mount_legacy_groups


if __name__ == "__main__":
    run()
