"""app-centric sdk surface: App, decorated handles, and execution primitives.

the public entry points here are:

- App: the registry a project defines its resources on
- @app.queue: queue-based serverless endpoint from a function
- @app.api: load-balanced serverless endpoint from a class (or asgi factory)
- @app.task: ephemeral pod compute from a function
- runpod.local_entrypoint: dev-session entry hook
- runpod.is_local: context check
- runpod.Queue / runpod.Api: stubs for resources deployed from elsewhere
"""

from .app import App, get_registered_apps
from .context import Context, current_context, is_local
from .entrypoint import local_entrypoint
from .errors import (
    AppError,
    EndpointNotFound,
    RemoteExecutionError,
    ScheduleNotSupported,
)
from .handles import ApiHandle, FunctionHandle
from .markers import delete, get, init, patch, post, put
from .schedule import schedule
from .spec import ResourceKind, ResourceSpec, RouteSpec
from .stubs import Api, Queue

__all__ = [
    "Api",
    "ApiHandle",
    "App",
    "AppError",
    "Context",
    "EndpointNotFound",
    "FunctionHandle",
    "Queue",
    "RemoteExecutionError",
    "ResourceKind",
    "ResourceSpec",
    "RouteSpec",
    "ScheduleNotSupported",
    "current_context",
    "delete",
    "get",
    "get_registered_apps",
    "init",
    "is_local",
    "local_entrypoint",
    "patch",
    "post",
    "put",
    "schedule",
]
