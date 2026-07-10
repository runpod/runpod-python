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


# commands whose output would be polluted by (or that manage) the
# update notice
_UPDATE_CHECK_EXCLUDED = frozenset({"dev", "update"})


@click.group()
@click.pass_context
def cli(ctx):
    """Runpod CLI: deploy and manage apps, endpoints, and pods."""
    if ctx.invoked_subcommand not in _UPDATE_CHECK_EXCLUDED:
        from runpod.rp_cli.update import start_background_check

        start_background_check()


def _fail(message: str) -> None:
    raise click.ClickException(message)


def _app_source(found, project_root: Path) -> str:
    """the file an app was discovered in, relative to the project."""
    source = getattr(found, "_source_file", None)
    if source is None:
        return ""
    try:
        return str(Path(source).resolve().relative_to(project_root))
    except ValueError:
        return str(source)


# ------------------------------------------------------------------ init


@cli.command()
@click.argument("project_name", required=False)
@click.option("--force", "-f", is_flag=True, help="Overwrite existing files.")
def init(project_name, force):
    """Create a new app project (PROJECT_NAME, or '.' for the cwd)."""
    from runpod.apps.init import create_project, detect_conflicts
    from runpod.rp_cli import console as ui

    if project_name is None or project_name == ".":
        project_dir = Path.cwd()
        name = project_dir.name
    else:
        project_dir = Path(project_name)
        name = project_name

    conflicts = detect_conflicts(project_dir)
    if conflicts and not force:
        listing = ", ".join(conflicts)
        _fail(
            f"{listing} already exist{'s' if len(conflicts) == 1 else ''} "
            f"in {project_dir}. pass --force to overwrite."
        )

    written = create_project(project_dir, name, overwrite=force)
    ui.success(
        f"initialized [white]{name}[/white] "
        f"[dim]{len(written)} files[/dim]"
    )
    ui.console.print()
    if project_dir != Path.cwd():
        ui.console.print(f"  [dim]cd {project_name}[/dim]")
    ui.console.print("  [dim]rp login[/dim]")
    ui.console.print("  [dim]rp dev main.py[/dim]")
    ui.console.print()


# ---------------------------------------------------------------- update


@cli.command()
@click.option(
    "--version", "-V", "version_opt", default=None, help="Target version."
)
def update(version_opt):
    """Update the runpod package to the latest (or a given) version."""
    from runpod.rp_cli import console as ui
    from runpod.rp_cli.update import (
        compare_versions,
        current_version,
        fetch_pypi_metadata,
        parse_version,
        run_install,
    )

    current = current_version()
    ui.console.print(f"current version: [white]{current}[/white]")

    with ui.console.status("[dim]checking pypi ...[/dim]"):
        try:
            latest, releases = fetch_pypi_metadata()
        except (ConnectionError, RuntimeError) as exc:
            _fail(str(exc))

    target = version_opt or latest
    if target not in releases:
        _fail(f"version '{target}' not found on pypi")
    if current == target:
        ui.console.print(f"already on [white]{target}[/white], nothing to do")
        return
    if (
        current != "unknown"
        and compare_versions(parse_version(target), parse_version(current)) < 0
    ):
        ui.warn(f"downgrading from {current} to {target}")

    with ui.console.status(f"[dim]installing runpod {target} ...[/dim]"):
        try:
            run_install(target)
        except Exception as exc:  # noqa: BLE001 - surface installer errors cleanly
            _fail(str(exc))
    ui.success(f"updated to [white]{target}[/white]")


# ---------------------------------------------------------------- deploy


@cli.command()
@click.argument("target", required=False, type=click.Path(path_type=Path))
@click.option("--env", "-e", "env", default=None, help="Target environment name.")
@click.option(
    "--python-version",
    default=None,
    help="Worker python version for dependency wheels (3.10-3.14). "
    "Defaults to the python running the cli.",
)
@click.option(
    "--exclude",
    default=None,
    help="Comma-separated packages to exclude from the artifact "
    "(they must then come from the worker image). Torch packages are "
    "excluded automatically on builtin gpu images.",
)
@click.option(
    "--build-only",
    is_flag=True,
    help="Build the artifact without deploying; writes "
    "<app>-artifact.tar.gz to the current directory.",
)
def deploy(target, env, python_version, exclude, build_only):
    """Package and deploy all apps found in TARGET (default: cwd)."""
    import logging

    from runpod.apps.deploy import build_artifact, deploy_app
    from runpod.apps.discovery import DiscoveryError, discover_apps
    from runpod.rp_cli import console as ui

    logging.getLogger("runpod.apps").setLevel(logging.WARNING)

    if python_version is None:
        from runpod.apps.images import (
            DEFAULT_PYTHON_VERSION,
            local_python_version,
        )

        try:
            python_version = local_python_version()
        except RuntimeError:
            # deployed calls are plain json (no pickle compat needed),
            # so an unsupported local interpreter falls back instead of
            # failing like dev does
            ui.warn(
                f"local python has no runtime image; building for "
                f"{DEFAULT_PYTHON_VERSION} (override with --python-version)"
            )
            python_version = DEFAULT_PYTHON_VERSION

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

    if build_only:
        for found in apps:
            ui.set_name_width(list(found.resources))
            events = ui.DeployEvents()
            try:
                artifact = build_artifact(
                    found,
                    project_root,
                    python_version=python_version,
                    exclude=exclude.split(",") if exclude else None,
                    events=events,
                    output=Path.cwd() / f"{found.name}-artifact.tar.gz",
                )
            except Exception as exc:  # noqa: BLE001 - surface engine errors cleanly
                raise click.ClickException(str(exc)) from exc
            finally:
                events.close()
            size_mb = artifact.stat().st_size / (1024 * 1024)
            ui.success(
                f"built [white]{found.name}[/white] "
                f"[dim]{artifact.name} ({size_mb:.1f} MB)[/dim]"
            )
        return

    if len(apps) > 1:
        ui.deploy_plan(
            [
                (
                    found.name,
                    _app_source(found, project_root),
                    len(found.resources),
                )
                for found in apps
            ]
        )

    for index, found in enumerate(apps):
        if index or len(apps) > 1:
            ui.console.print()
        ui.set_name_width(list(found.resources))
        ui.deploy_banner(
            found.name,
            env or found.env,
            [(h.spec.name, h.spec.kind.value) for h in found.resources.values()],
            source=_app_source(found, project_root),
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
            except Exception as exc:  # noqa: BLE001 - surface engine errors cleanly
                raise click.ClickException(str(exc)) from exc
            finally:
                events.close()
        ui.success(
            f"[bold white]{result.app_name}/{env or found.env}[/bold white] "
            f"is live [dim]{t.elapsed:.1f}s[/dim]"
        )
        if result.endpoints:
            ui.console.print()
            w = max(len(n) for n in result.endpoints)
            for name, endpoint_id in sorted(result.endpoints.items()):
                ui.console.print(
                    f"  [white]{name:<{w}}[/white]  "
                    f"{ui.endpoint_link(endpoint_id)}"
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
        # each rescan registers entrypoints anew; clearing first keeps
        # get_entrypoint() reflecting the current file, not stale runs
        from runpod.apps.entrypoint import _clear_entrypoints

        _clear_entrypoints()
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

    # daemon stdin reader: a blocked readline must never keep the
    # process alive after ctrl-c (executor threads are joined at
    # loop shutdown; a daemon thread is not)
    import queue as _queue
    import threading

    stdin_lines: "_queue.Queue[str]" = _queue.Queue()

    def _stdin_reader() -> None:
        for line in sys.stdin:
            stdin_lines.put(line)
        stdin_lines.put("")  # eof marker

    threading.Thread(target=_stdin_reader, daemon=True).start()

    async def _wait_for_rerun(watcher: "FileWatcher") -> str:
        """race the enter key against a file change."""
        eof = False
        while True:
            if watcher.changed():
                return "changed"
            if not eof:
                try:
                    line = stdin_lines.get_nowait()
                    if line == "":
                        # piped stdin / ci: no interactive re-runs,
                        # keep watching files only
                        eof = True
                    else:
                        return "enter"
                except _queue.Empty:
                    # no keypress this tick; fall through to the sleep
                    pass
            await asyncio.sleep(0.5)

    async def _run_entrypoint_cancellable(fn) -> None:
        """drive the entrypoint on a daemon thread.

        the entrypoint is user code full of blocking .remote() calls;
        running it inline would pin the main loop and make ctrl-c
        undeliverable (asyncio's sigint handler cancels the main task,
        which needs an await point). a daemon thread keeps the loop
        free, and cancellation simply abandons the in-flight call.
        """
        loop = asyncio.get_running_loop()
        done: asyncio.Future = loop.create_future()

        def _finish(exc: "BaseException | None") -> None:
            if done.done():
                return
            if exc is None:
                done.set_result(None)
            else:
                done.set_exception(exc)

        def _runner() -> None:
            try:
                run_entrypoint(fn)
            # BaseException on purpose: SystemExit/KeyboardInterrupt from
            # the entrypoint must reach the loop, not die on this thread
            except BaseException as exc:  # noqa: BLE001 - marshalled to the loop
                # bind the exception now: `as exc` is unbound once the
                # except block exits, before the loop callback runs
                loop.call_soon_threadsafe(_finish, exc)
            else:
                loop.call_soon_threadsafe(_finish, None)

        threading.Thread(target=_runner, daemon=True).start()
        await done

    async def _session() -> int:
        nonlocal apps, entrypoint
        ui.set_name_width(_all_resource_names(apps))
        session = DevSession(apps, events=ui.DevEvents())
        watcher = FileWatcher([module.parent])

        ui.dev_banner([a.name for a in apps], str(module.name))
        await session.start()
        ui.resources_table(_table_rows(session))

        try:
            while True:
                ui.entrypoint_header(getattr(entrypoint, "__name__", ""))
                with ui.Timer() as t:
                    try:
                        await _run_entrypoint_cancellable(entrypoint)
                        ui.entrypoint_success(t.so_far)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - dev loop survives user errors
                        ui.entrypoint_failure(t.so_far, str(exc))
                        if once:
                            return 1
                if once:
                    return 0
                reason = await _wait_for_rerun(watcher)
                if reason == "changed":
                    ui.reload_banner(str(module.name))
                    apps, entrypoint = _scan()
                    ui.set_name_width(_all_resource_names(apps))
                    await session.refresh(apps)
        finally:
            events = ui.CleanupEvents()
            await session.stop(events=events)
            events.close()

    try:
        sys.exit(asyncio.run(_session()))
    except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
        ui.console.print()
    except Exception as exc:  # noqa: BLE001 - surface engine errors cleanly
        raise click.ClickException(str(exc)) from exc


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
        # ctrl-c ends the log follow; not an error
        pass


# ----------------------------------------------------------------- login


@cli.command()
@click.option(
    "--no-open", is_flag=True, help="Print the url instead of opening a browser."
)
@click.option(
    "--api-key",
    "api_key_opt",
    default=None,
    help="Store this API key directly (skips the browser flow).",
)
def login(no_open, api_key_opt):
    """Authenticate with Runpod and store the API key.

    Opens the Runpod console for browser approval by default; pass
    --api-key to store a key directly.
    """
    from runpod.apps.auth import LoginError, browser_login
    from runpod.cli.groups.config.functions import set_credentials
    from runpod.rp_cli import console as ui

    if api_key_opt:
        try:
            set_credentials(api_key_opt, overwrite=True)
        except ValueError as exc:
            _fail(str(exc))
        ui.success("credentials saved to [dim]~/.runpod/config.toml[/dim]")
        return

    def _show_url(url: str) -> None:
        ui.console.print()
        ui.console.print(" [white]authorize in your browser:[/white]")
        ui.console.print(f" [accent.light][link={url}]{url}[/link][/accent.light]")
        ui.console.print()
        if not no_open:
            click.launch(url)

    try:
        with ui.console.status("[dim]waiting for approval ...[/dim]"):
            api_key = asyncio.run(browser_login(on_url=_show_url))
        set_credentials(api_key, overwrite=True)
    except (LoginError, ValueError) as exc:
        _fail(str(exc))
    ui.success("logged in, credentials saved to [dim]~/.runpod/config.toml[/dim]")


# ---------------------------------------------------------- app / env


def _fmt_ts(value) -> str:
    if not value:
        return "-"
    text = str(value)
    return text[:10] if len(text) >= 10 else text


@cli.group()
def app():
    """Manage deployed apps."""


@app.command(name="list")
def app_list():
    """List all apps and their environments."""
    from runpod.apps.manage import list_apps
    from runpod.rp_cli import console as ui

    try:
        apps = asyncio.run(list_apps())
    except Exception as exc:  # noqa: BLE001 - surface engine errors cleanly
        raise click.ClickException(str(exc)) from exc

    if not apps:
        ui.console.print("\n  no apps deployed. run [white]rp deploy[/white]\n")
        return

    width = max(len(a["name"]) for a in apps)
    ui.console.print()
    for entry in apps:
        envs = entry.get("flashEnvironments") or []
        names = ", ".join(e["name"] for e in envs) or "-"
        ui.console.print(
            f"  [white]{entry['name']:<{width}}[/white]  [dim]{names}[/dim]"
        )
    ui.console.print()


@app.command(name="get")
@click.argument("app_name")
def app_get(app_name):
    """Show one app with its environments."""
    from runpod.apps.manage import get_app
    from runpod.rp_cli import console as ui

    try:
        entry = asyncio.run(get_app(app_name))
    except Exception as exc:  # noqa: BLE001 - surface engine errors cleanly
        raise click.ClickException(str(exc)) from exc

    envs = entry.get("flashEnvironments") or []
    ui.console.print()
    ui.console.print(f"  [white]{entry['name']}[/white]")
    if envs:
        width = max(len(e["name"]) for e in envs)
        for env_entry in envs:
            build = (env_entry.get("activeBuildId") or "no build")[:12]
            ui.console.print(
                f"    [white]{env_entry['name']:<{width}}[/white]  [dim]{build}[/dim]"
            )
    ui.console.print()


@app.command(name="delete")
@click.argument("app_name")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
def app_delete(app_name, yes):
    """Delete an app, undeploying every environment in it."""
    from runpod.apps.manage import delete_app
    from runpod.rp_cli import console as ui

    if not yes:
        click.confirm(
            f"delete app '{app_name}' and all its environments?", abort=True
        )

    events = ui.CleanupEvents()
    try:
        result = asyncio.run(delete_app(app_name, events=events))
    except Exception as exc:  # noqa: BLE001 - surface engine errors cleanly
        raise click.ClickException(str(exc)) from exc
    finally:
        events.close()

    if result.failures:
        for failure in result.failures:
            ui.error(failure)
        raise click.ClickException("undeploy incomplete; app kept")
    ui.success(
        f"deleted [white]{app_name}[/white] "
        f"[dim]{result.endpoints_deleted} endpoints removed[/dim]"
    )


@cli.group()
def registry():
    """Manage container registry credentials (private images)."""


@registry.command(name="add")
@click.argument("name", required=False)
@click.option("--username", default=None, help="Registry username (prompted if omitted).")
@click.option("--password", default=None, help="Registry password/token (prompted if omitted).")
def registry_add(name, username, password):
    """Create a registry credential. Reference it with registry_auth=NAME."""
    from runpod.apps.api import AppsApiClient
    from runpod.rp_cli import console as ui

    if name is None:
        name = click.prompt("name")
    if username is None:
        username = click.prompt("username")
    if password is None:
        password = click.prompt("password", hide_input=True)

    try:
        asyncio.run(
            AppsApiClient().create_registry_auth(name, username, password)
        )
    except Exception as exc:  # noqa: BLE001 - surface engine errors cleanly
        raise click.ClickException(str(exc)) from exc
    ui.success(
        f"registry credential [white]{name}[/white] created, "
        f'[dim]use registry_auth="{name}"[/dim]'
    )


@registry.command(name="list")
def registry_list():
    """List registry credential names."""
    from runpod.apps.api import AppsApiClient
    from runpod.rp_cli import console as ui

    try:
        creds = asyncio.run(AppsApiClient().list_registry_auths())
    except Exception as exc:  # noqa: BLE001 - surface engine errors cleanly
        raise click.ClickException(str(exc)) from exc

    if not creds:
        ui.console.print(
            "\n  no registry credentials. run [white]rp registry add <name>[/white]\n"
        )
        return
    ui.console.print()
    for entry in sorted(creds, key=lambda c: c["name"]):
        ui.console.print(f"  [white]{entry['name']}[/white]  [dim]{entry['id']}[/dim]")
    ui.console.print()


@registry.command(name="delete")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
def registry_delete(name, yes):
    """Delete a registry credential by name."""
    from runpod.apps.api import AppsApiClient
    from runpod.rp_cli import console as ui

    async def _delete():
        client = AppsApiClient()
        creds = await client.list_registry_auths()
        match = next((c for c in creds if c["name"] == name), None)
        if match is None:
            _fail(f"no registry credential named '{name}'")
        await client.delete_registry_auth(match["id"])

    if not yes:
        click.confirm(f"delete registry credential '{name}'?", abort=True)
    try:
        asyncio.run(_delete())
    except click.ClickException:
        raise
    except Exception as exc:  # noqa: BLE001 - surface engine errors cleanly
        raise click.ClickException(str(exc)) from exc
    ui.success(f"deleted registry credential [white]{name}[/white]")


@cli.group()
def secret():
    """Manage platform secrets (encrypted env values)."""


@secret.command(name="add")
@click.argument("name", required=False)
@click.option("--value", default=None, help="Secret value (prompted if omitted).")
@click.option("--description", default="", help="Optional description.")
def secret_add(name, value, description):
    """Create a secret. Reference it with runpod.Secret(NAME).

    Prompts for anything not provided: bare `rp secret add` asks for
    name and value, `rp secret add NAME` asks for just the value.
    """
    from runpod.apps.api import AppsApiClient
    from runpod.rp_cli import console as ui

    if name is None:
        name = click.prompt("name")
    if value is None:
        value = click.prompt("value", hide_input=True)

    try:
        asyncio.run(AppsApiClient().create_secret(name, value, description))
    except Exception as exc:  # noqa: BLE001 - surface engine errors cleanly
        raise click.ClickException(str(exc)) from exc
    ui.success(
        f"secret [white]{name}[/white] created, "
        f"[dim]use runpod.Secret(\"{name}\")[/dim]"
    )


@secret.command(name="list")
def secret_list():
    """List secret names (values are never shown)."""
    from runpod.apps.api import AppsApiClient
    from runpod.rp_cli import console as ui

    try:
        secrets = asyncio.run(AppsApiClient().list_secrets())
    except Exception as exc:  # noqa: BLE001 - surface engine errors cleanly
        raise click.ClickException(str(exc)) from exc

    if not secrets:
        ui.console.print("\n  no secrets. run [white]rp secret add <name>[/white]\n")
        return
    ui.console.print()
    width = max(len(s["name"]) for s in secrets)
    for entry in sorted(secrets, key=lambda s: s["name"]):
        description = entry.get("description") or ""
        ui.console.print(
            f"  [white]{entry['name']:<{width}}[/white]  [dim]{description}[/dim]"
        )
    ui.console.print()


@secret.command(name="delete")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
def secret_delete(name, yes):
    """Delete a secret by name."""
    from runpod.apps.api import AppsApiClient
    from runpod.rp_cli import console as ui

    async def _delete():
        client = AppsApiClient()
        secrets = await client.list_secrets()
        match = next((s for s in secrets if s["name"] == name), None)
        if match is None:
            _fail(f"no secret named '{name}'")
        await client.delete_secret(match["id"])

    if not yes:
        click.confirm(f"delete secret '{name}'?", abort=True)
    try:
        asyncio.run(_delete())
    except click.ClickException:
        raise
    except Exception as exc:  # noqa: BLE001 - surface engine errors cleanly
        raise click.ClickException(str(exc)) from exc
    ui.success(f"deleted secret [white]{name}[/white]")


@cli.group()
def env():
    """Manage app environments."""


def _resolve_app_name(app_name) -> str:
    """--app flag or the single app discovered in cwd."""
    if app_name:
        return app_name
    from runpod.apps.discovery import DiscoveryError, discover_apps

    try:
        apps = discover_apps(Path.cwd())
    except DiscoveryError:
        apps = []
    if len(apps) == 1:
        return apps[0].name
    _fail("pass --app <name> (no unique app found in the current directory)")


@env.command(name="list")
@click.option("--app", "-a", "app_name", default=None, help="App name.")
def env_list(app_name):
    """List environments for an app."""
    from runpod.apps.manage import get_app
    from runpod.rp_cli import console as ui

    name = _resolve_app_name(app_name)
    try:
        entry = asyncio.run(get_app(name))
    except Exception as exc:  # noqa: BLE001 - surface engine errors cleanly
        raise click.ClickException(str(exc)) from exc

    envs = entry.get("flashEnvironments") or []
    if not envs:
        ui.console.print(
            f"\n  no environments in [white]{name}[/white]. run [white]rp deploy[/white]\n"
        )
        return
    ui.console.print()
    width = max(len(e["name"]) for e in envs)
    for env_entry in envs:
        build = (env_entry.get("activeBuildId") or "-")[:12]
        ui.console.print(
            f"  [white]{env_entry['name']:<{width}}[/white] "
            f"[dim]{build}, {_fmt_ts(env_entry.get('createdAt'))}[/dim]"
        )
    ui.console.print()


@env.command(name="get")
@click.argument("env_name")
@click.option("--app", "-a", "app_name", default=None, help="App name.")
def env_get(env_name, app_name):
    """Show an environment with its endpoints."""
    from runpod.apps.manage import get_environment
    from runpod.rp_cli import console as ui

    name = _resolve_app_name(app_name)
    try:
        entry = asyncio.run(get_environment(name, env_name))
    except Exception as exc:  # noqa: BLE001 - surface engine errors cleanly
        raise click.ClickException(str(exc)) from exc

    ui.console.print()
    ui.console.print(
        f"  [white]{name}/{entry['name']}[/white] "
        f"[dim]build {(entry.get('activeBuildId') or '-')[:12]}[/dim]"
    )
    endpoints = entry.get("endpoints") or []
    if endpoints:
        width = max(len(e["name"]) for e in endpoints)
        for endpoint in endpoints:
            ui.console.print(
                f"    [white]{endpoint['name']:<{width}}[/white]  "
                f"{ui.endpoint_link(endpoint['id'])}"
            )
    ui.console.print()


@env.command(name="add")
@click.argument("env_name")
@click.option("--app", "-a", "app_name", default=None, help="App name.")
def env_add(env_name, app_name):
    """Create a new environment in an app."""
    from runpod.apps.api import AppsApiClient
    from runpod.apps.manage import get_app
    from runpod.rp_cli import console as ui

    name = _resolve_app_name(app_name)

    async def _create():
        client = AppsApiClient()
        entry = await get_app(name, api=client)
        return await client.create_environment(entry["id"], env_name)

    try:
        asyncio.run(_create())
    except Exception as exc:  # noqa: BLE001 - surface engine errors cleanly
        raise click.ClickException(str(exc)) from exc
    ui.success(f"created [white]{name}/{env_name}[/white]")


@env.command(name="delete")
@click.argument("env_name")
@click.option("--app", "-a", "app_name", default=None, help="App name.")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
def env_delete(env_name, app_name, yes):
    """Undeploy and delete an environment."""
    from runpod.apps.manage import undeploy_environment
    from runpod.rp_cli import console as ui

    name = _resolve_app_name(app_name)
    if not yes:
        click.confirm(
            f"undeploy and delete '{name}/{env_name}'?", abort=True
        )

    events = ui.CleanupEvents()
    try:
        result = asyncio.run(
            undeploy_environment(name, env_name, events=events)
        )
    except Exception as exc:  # noqa: BLE001 - surface engine errors cleanly
        raise click.ClickException(str(exc)) from exc
    finally:
        events.close()

    if result.failures:
        for failure in result.failures:
            ui.error(failure)
        raise click.ClickException("undeploy incomplete; environment kept")
    ui.success(
        f"deleted [white]{name}/{env_name}[/white] "
        f"[dim]{result.endpoints_deleted} endpoints removed[/dim]"
    )


@cli.command()
@click.option("--app", "-a", "app_name", default=None, help="App name.")
@click.option("--env", "-e", "env_name", default="default", show_default=True)
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
def undeploy(app_name, env_name, yes):
    """Tear down a deployed environment's endpoints.

    Deletes the environment's endpoints and the environment itself;
    the app and its build history remain.
    """
    from runpod.apps.manage import undeploy_environment
    from runpod.rp_cli import console as ui

    name = _resolve_app_name(app_name)
    if not yes:
        click.confirm(f"undeploy '{name}/{env_name}'?", abort=True)

    events = ui.CleanupEvents()
    try:
        result = asyncio.run(
            undeploy_environment(name, env_name, events=events)
        )
    except Exception as exc:  # noqa: BLE001 - surface engine errors cleanly
        raise click.ClickException(str(exc)) from exc
    finally:
        events.close()

    if result.failures:
        for failure in result.failures:
            ui.error(failure)
        raise click.ClickException("undeploy incomplete")
    ui.success(
        f"undeployed [white]{name}/{env_name}[/white] "
        f"[dim]{result.endpoints_deleted} endpoints removed[/dim]"
    )


# -------------------------------------------------------- legacy groups


def _mount_groups() -> None:
    from runpod.cli.groups.pod.commands import pod_cli
    from runpod.cli.groups.ssh.commands import ssh_cli

    for command in (ssh_cli, pod_cli):
        cli.add_command(command)


_mount_groups()


def run() -> None:
    """console-script entry point."""
    cli()


if __name__ == "__main__":
    run()
