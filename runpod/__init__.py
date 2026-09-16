"""Allows runpod to be imported as a module."""

import importlib
import logging
import os

from .version import __version__

# public names resolved on first access (PEP 562). keeping the top-level
# import light means `import runpod` (and the deployed worker that does
# it) pulls only what it actually touches, not the whole sdk/cli closure.
_LAZY_ATTRS = {
    # api control-plane commands
    "create_container_registry_auth": "runpod.api.ctl_commands",
    "create_endpoint": "runpod.api.ctl_commands",
    "create_pod": "runpod.api.ctl_commands",
    "create_template": "runpod.api.ctl_commands",
    "delete_container_registry_auth": "runpod.api.ctl_commands",
    "get_endpoints": "runpod.api.ctl_commands",
    "get_gpu": "runpod.api.ctl_commands",
    "get_gpus": "runpod.api.ctl_commands",
    "get_pod": "runpod.api.ctl_commands",
    "get_pods": "runpod.api.ctl_commands",
    "get_user": "runpod.api.ctl_commands",
    "resume_pod": "runpod.api.ctl_commands",
    "stop_pod": "runpod.api.ctl_commands",
    "terminate_pod": "runpod.api.ctl_commands",
    "update_container_registry_auth": "runpod.api.ctl_commands",
    "update_endpoint_template": "runpod.api.ctl_commands",
    "update_user_settings": "runpod.api.ctl_commands",
    # config helpers
    "check_credentials": "runpod.cli.groups.config.functions",
    "get_credentials": "runpod.cli.groups.config.functions",
    "set_credentials": "runpod.cli.groups.config.functions",
    # endpoint clients
    "AsyncioEndpoint": "runpod.endpoint",
    "AsyncioJob": "runpod.endpoint",
    "Endpoint": "runpod.endpoint",
    # apps surface
    "Api": "runpod.apps",
    "App": "runpod.apps",
    "DataCenter": "runpod.apps",
    "EndpointNotFound": "runpod.apps",
    "Job": "runpod.apps",
    "Model": "runpod.apps",
    "Queue": "runpod.apps",
    "Secret": "runpod.apps",
    "Volume": "runpod.apps",
    "is_local": "runpod.apps",
    "local_entrypoint": "runpod.apps",
    "schedule": "runpod.apps",
    "CpuInstanceType": "runpod.apps.gpu",
    "GpuGroup": "runpod.apps.gpu",
    "GpuType": "runpod.apps.gpu",
    "delete": "runpod.apps.markers",
    "get": "runpod.apps.markers",
    "init": "runpod.apps.markers",
    "patch": "runpod.apps.markers",
    "post": "runpod.apps.markers",
    "put": "runpod.apps.markers",
    # logger
    "RunPodLogger": "runpod.serverless.modules.rp_logger",
    # submodule
    "serverless": "runpod.serverless",
}

# lazy names resolve through __getattr__, so __all__ derives from the
# lazy table plus the eagerly-defined module attributes
__all__ = [
    *_LAZY_ATTRS,
    "__version__",
    "SSH_KEY_PATH",
    "profile",
    "api_key",
    "endpoint_url_base",
]


def _load_api_key():
    """resolve the api key from the environment, then stored credentials.

    the credential lookup is deferred (and tolerant of a missing cli
    dependency closure) so a bare `import runpod` never drags in the
    config/cli modules; the deployed worker gets its key from the env.
    """
    key = os.environ.get("RUNPOD_API_KEY")
    if key:
        return key
    try:
        creds = importlib.import_module(
            "runpod.cli.groups.config.functions"
        ).get_credentials(profile)
    except Exception:  # noqa: BLE001 - no stored key is a valid state
        return None
    return creds["api_key"] if creds else None


def __getattr__(name):
    module_path = _LAZY_ATTRS.get(name)
    if module_path is None:
        raise AttributeError(f"module 'runpod' has no attribute {name!r}")
    module = importlib.import_module(module_path)
    value = module if module_path == f"runpod.{name}" else getattr(module, name)
    globals()[name] = value
    return value


# ------------------------------- Config Paths ------------------------------- #
SSH_KEY_PATH = os.path.expanduser("~/.runpod/ssh")

profile = "default"  # pylint: disable=invalid-name

api_key = _load_api_key()  # pylint: disable=invalid-name

endpoint_url_base = os.environ.get(
    "RUNPOD_ENDPOINT_BASE_URL", "https://api.runpod.ai/v2"
)  # pylint: disable=invalid-name


# --------------------------- Force Logging Levels --------------------------- #
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("paramiko").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.WARNING)
