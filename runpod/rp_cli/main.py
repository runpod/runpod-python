"""the `rp` / `runpod` cli.

a single plain-click command tree: app commands (deploy, dev, logs,
login) and the pod/ssh/exec/config groups all hang off one root, so
help, errors, and exit codes behave identically at every level. rich
is used only for runtime output (progress, lifecycle lines), never for
help rendering.
"""

import asyncio
import os
import sys
from pathlib import Path

import click


@click.group()
def cli():
    """Runpod CLI: deploy and manage apps, endpoints, and pods."""


def _fail(message: str) -> None:
    raise click.ClickException(message)


# ---------------------------------------------------------------- deploy


@cli.command()
@click.argument("target", required=False, type=click.Path(path_type=Path))
@click.option("--env", "-e", "env", default=None, help="Target environment name.")
@click.option(
    "--python-version",
    default="3.12",
    show_default=True,
    help="Worker python version for dependency wheels (3.10-3.14).",
)
@click.option(
    "--exclude",
    default=None,
    help="Comma-separated packages to exclude from the artifact "
    "(they must then come from the worker image). Torch packages are "
    "excluded automatically on builtin gpu images.",
)
def deploy(target, env, python_version, exclude):
    """Package and deploy all apps found in TARGET (default: cwd)."""
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
            '    app = App("my-app")'
        )

    for found in apps:
        ui.set_name_width(list(found.resources))
        ui.deploy_banner(
            found.name,
            env or found.env,
            [(h.spec.name, h.spec.kind.value) for h in found.resources.values()],
        )
        events = ui.DeployEvents()
        with ui.Timer() as t:
            try:
                result = asyncio.run(
                    deploy_app(
                        found,
                        project_root,
                        env_name=env,
                        python_version=python_version,
                        exclude=exclude.split(",") if exclude else None,
                        events=events,
                    )
                )
            finally:
                events.close()
        ui.success(
            f"build [accent.light]{result.build_id}[/accent.light] live on "
            f"[bold white]{result.app_name}/{env or found.env}[/bold white] "
            f"[dim]{t.elapsed:.1f}s[/dim]"
        )
    ui.console.print()


# ------------------------------------------------------------------- dev


@cli.command()
@click.argument("module", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--once",
    is_flag=True,
    help="Run the entrypoint once, tear down, and exit (for scripts/CI).",
)
def dev(module, once):
    """Start an interactive dev session for MODULE.

    Provisions temporary live endpoints, runs the module's
    @runpod.local_entrypoint, watches for file changes (re-scanning and
    refreshing endpoints so requests run fresh code), and deletes the
    endpoints on exit.
    """
    import logging

    from runpod.apps.dev import DevSession
    from runpod.apps.discovery import DiscoveryError, discover_apps
    from runpod.apps.entrypoint import get_entrypoint, run_entrypoint
    from runpod.apps.watch import FileWatcher
    from runpod.rp_cli import console as ui

    logging.getLogger("runpod.apps").setLevel(logging.WARNING)

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
                "    async def main(): ..."
            )
        return apps, entrypoint

    apps, entrypoint = _scan()

    def _all_resource_names(app_list) -> list:
        return [
            handle.spec.name
            for a in app_list
            for handle in a.resources.values()
        ]

    def _table_rows(session: "DevSession") -> list:
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

    async def _wait_for_rerun(watcher: "FileWatcher") -> str:
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

    async def _wait_for_change(watcher: "FileWatcher") -> str:
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
        ui.resources_table(_table_rows(session))

        runs = 0
        reloads = 0
        session_timer = ui.Timer().__enter__()
        try:
            while True:
                ui.entrypoint_header()
                runs += 1
                with ui.Timer() as t:
                    try:
                        run_entrypoint(entrypoint)
                        ui.entrypoint_success(t.so_far)
                    except Exception as exc:  # noqa: BLE001 - dev loop survives user errors
                        ui.entrypoint_failure(t.so_far, str(exc))
                        if once:
                            return 1
                if once:
                    return 0
                ui.dev_hints()
                reason = await _wait_for_rerun(watcher)
                if reason == "changed":
                    reloads += 1
                    ui.reload_flash()
                    apps, entrypoint = _scan()
                    ui.set_name_width(_all_resource_names(apps))
                    await session.refresh(apps)
        finally:
            session_timer.__exit__()
            ui.session_summary(runs, reloads, session_timer.elapsed)
            ui.info("cleaning up dev endpoints ...")
            await session.stop()

    try:
        sys.exit(asyncio.run(_session()))
    except (KeyboardInterrupt, EOFError):
        ui.console.print()
        ui.info("session ended.")


# ------------------------------------------------------------------ logs


@cli.command()
@click.argument("pod_id")
@click.option("--follow", "-f", is_flag=True, help="Stream logs as they arrive.")
@click.option(
    "--type",
    "log_type",
    default="all",
    show_default=True,
    type=click.Choice(["all", "container", "system"]),
    help="Log source.",
)
@click.option("--tail", default=100, show_default=True, help="Lines of backfill.")
def logs(pod_id, follow, log_type, tail):
    """Show a pod's container and system logs."""
    from runpod.apps.logs import pod_logs, stream_pod_logs

    async def _run():
        if follow:
            async for entry in stream_pod_logs(
                pod_id, log_type=log_type, tail=tail
            ):
                source = entry.get("source", "?")
                click.echo(f"[{source}] {entry.get('line', '')}")
        else:
            logs_data = await pod_logs(pod_id, log_type=log_type)
            for source in ("system", "container"):
                for line in logs_data.get(source) or []:
                    click.echo(f"[{source}] {line}")

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


# ----------------------------------------------------------------- login


@cli.command()
def login():
    """Authenticate with Runpod and store the API key."""
    from runpod.cli.groups.config.functions import set_credentials

    api_key = click.prompt("Runpod API key", hide_input=True)
    try:
        set_credentials(api_key, overwrite=True)
    except ValueError as exc:
        _fail(str(exc))
    click.echo("credentials saved to ~/.runpod/config.toml")


# -------------------------------------------------------- legacy groups


def _mount_groups() -> None:
    from runpod.cli.groups.config.commands import config_wizard
    from runpod.cli.groups.exec.commands import exec_cli
    from runpod.cli.groups.pod.commands import pod_cli
    from runpod.cli.groups.ssh.commands import ssh_cli

    for command in (config_wizard, ssh_cli, pod_cli, exec_cli):
        cli.add_command(command)


_mount_groups()


def run() -> None:
    """console-script entry point."""
    cli()


if __name__ == "__main__":
    run()
