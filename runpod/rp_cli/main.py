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
    python_version: str = typer.Option(
        "3.12",
        "--python-version",
        help="Worker python version for dependency wheels (3.10-3.14).",
    ),
    exclude: Optional[str] = typer.Option(
        None,
        "--exclude",
        help="Comma-separated packages to exclude from the artifact "
        "(they must then come from the worker image). torch packages "
        "are always excluded.",
    ),
):
    """package and deploy all apps found in TARGET."""
    import logging

    from runpod.apps.deploy import deploy_app
    from runpod.apps.discovery import DiscoveryError, discover_apps
    from runpod.rp_cli import console as ui

    logging.getLogger("runpod.apps").setLevel(logging.WARNING)

    target = (target or Path.cwd()).resolve()
    if not target.exists():
        _fail(f"{target} does not exist")

    project_root = target if target.is_dir() else target.parent

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
        resource_names = list(found.resources)
        ui.set_name_width(resource_names)
        ui.console.print()
        ui.console.print(
            f"[bold]{found.name}[/bold] [dim]-> {env or found.env}[/dim]"
        )
        with ui.Timer() as t:
            result = asyncio.run(
                deploy_app(
                    found,
                    project_root,
                    env_name=env,
                    python_version=python_version,
                    exclude=exclude.split(",") if exclude else None,
                    events=ui.DeployEvents(),
                )
            )
        for name, endpoint_id in sorted(result.endpoints.items()):
            ui.resource_ready(name, endpoint_id)
        ui.success(
            f"build [white]{result.build_id}[/white] live on "
            f"[white]{result.app_name}/{env or found.env}[/white] "
            f"[dim]{t.elapsed:.1f}s[/dim]"
        )
    ui.console.print()


@app.command()
def dev(
    module: Path = typer.Argument(..., help="Python module to run."),
    once: bool = typer.Option(
        False,
        "--once",
        help="Run the entrypoint once, tear down, and exit (for scripts/CI).",
    ),
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
    from runpod.rp_cli import console as ui

    module = module.resolve()
    if not module.is_file():
        _fail(f"{module} is not a file")

    os.environ["RUNPOD_DEV_SESSION"] = "1"

    # the console renders lifecycle lines; keep the module loggers to
    # warnings so output isn't duplicated
    import logging

    logging.getLogger("runpod.apps").setLevel(logging.WARNING)

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

    def _all_resource_names(app_list) -> list:
        return [
            handle.spec.name
            for a in app_list
            for handle in a.resources.values()
        ]

    def _table_rows(session: DevSession) -> list:
        rows = []
        for a in session.apps:
            for handle in a.resources.values():
                spec = handle.spec
                hardware = ",".join(spec.cpu or spec.gpu or ["any"])
                endpoint_id = session._endpoints.get(
                    f"dev-{a.name}-{spec.name}", ""
                ) or ("per-call" if spec.kind.value == "task" else "")
                rows.append((spec.name, spec.kind.value, hardware, endpoint_id))
        return rows

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
                    # eof (piped stdin, ci): interactive re-run is
                    # impossible, wait on file changes only
                    if not stdin_task.result():
                        return await _wait_for_change(watcher)
                    return "enter"
        finally:
            stdin_task.cancel()

    async def _wait_for_change(watcher: FileWatcher) -> str:
        while True:
            if watcher.changed():
                return "changed"
            await asyncio.sleep(0.5)

    async def _session() -> int:
        nonlocal apps, entrypoint
        ui.set_name_width(_all_resource_names(apps))
        session = DevSession(apps, events=ui.DevEvents())
        watcher = FileWatcher([module.parent])

        ui.dev_banner([a.name for a in apps], str(module.name))
        await session.start()
        ui.console.print()
        ui.dev_resources_table(_table_rows(session))

        try:
            while True:
                ui.rule("entrypoint")
                with ui.Timer() as t:
                    try:
                        run_entrypoint(entrypoint)
                        ui.success(f"entrypoint [dim]{t.so_far:.1f}s[/dim]")
                    except Exception as exc:  # noqa: BLE001 - dev loop survives user errors
                        ui.error(f"entrypoint failed [dim]{t.so_far:.1f}s[/dim]")
                        ui.console.print(f"  [red]{exc}[/red]")
                        if once:
                            return 1
                if once:
                    return 0
                ui.rule()
                ui.dev_hints()
                reason = await _wait_for_rerun(watcher)
                if reason == "changed":
                    ui.info("[blue]change detected[/blue] reloading ...")
                    apps, entrypoint = _scan()
                    ui.set_name_width(_all_resource_names(apps))
                    await session.refresh(apps)
        finally:
            ui.console.print()
            ui.info("cleaning up dev endpoints ...")
            await session.stop()

    try:
        raise typer.Exit(asyncio.run(_session()))
    except (KeyboardInterrupt, EOFError):
        ui.console.print()
        ui.info("session ended.")


@app.command()
def logs(
    pod_id: str = typer.Argument(..., help="Pod id to fetch logs for."),
    follow: bool = typer.Option(
        False, "--follow", "-f", help="Stream logs as they arrive."
    ),
    log_type: str = typer.Option(
        "all", "--type", help="all, container, or system."
    ),
    tail: int = typer.Option(100, "--tail", help="Lines of backfill."),
):
    """show a pod's container and system logs (host api)."""
    from runpod.apps.logs import pod_logs, stream_pod_logs

    async def _run():
        if follow:
            async for entry in stream_pod_logs(
                pod_id, log_type=log_type, tail=tail
            ):
                source = entry.get("source", "?")
                _echo(f"[{source}] {entry.get('line', '')}")
        else:
            logs_data = await pod_logs(pod_id, log_type=log_type)
            for source in ("system", "container"):
                for line in logs_data.get(source) or []:
                    _echo(f"[{source}] {line}")

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


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
