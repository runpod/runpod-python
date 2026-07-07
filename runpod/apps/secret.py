"""platform secrets: encrypted values injected into worker env vars.

a Secret marks an env value for reference-syntax rendering; the
platform decrypts it when the worker boots. values never travel
through the sdk.

    @app.queue(env={"HF_TOKEN": runpod.Secret("hf-token")})
    def download(): ...

the worker sees a plain HF_TOKEN env var with the decrypted value.
create and manage secrets with `rp secret add/list/rm`.
"""

import re
from typing import Any, Dict, List, Optional

from .errors import AppError

# the platform's env reference syntax (decrypted server-side at boot)
_REFERENCE_TEMPLATE = "{{{{ RUNPOD_SECRET_{name} }}}}"
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class SecretError(AppError):
    pass


class Secret:
    """a reference to a platform secret by name."""

    def __init__(self, name: str):
        if not name or not isinstance(name, str):
            raise SecretError("secret name must be a non-empty string")
        if not _NAME_RE.match(name):
            raise SecretError(
                f"invalid secret name '{name}': letters, digits, "
                f"underscores, dots, and dashes only"
            )
        self.name = name

    @property
    def reference(self) -> str:
        """the env-var value the platform substitutes at boot."""
        return _REFERENCE_TEMPLATE.format(name=self.name)

    def __repr__(self) -> str:
        return f"<Secret {self.name!r}>"


def render_env(env: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """render an env mapping, expanding Secret values to references.

    plain values stringify; Secret values become the platform's
    reference syntax.
    """
    if not env:
        return {}
    rendered: Dict[str, str] = {}
    for key, value in env.items():
        if isinstance(value, Secret):
            rendered[key] = value.reference
        else:
            rendered[key] = str(value)
    return rendered


def secret_names(env: Optional[Dict[str, Any]]) -> List[str]:
    """names of every Secret referenced in an env mapping."""
    if not env:
        return []
    return [v.name for v in env.values() if isinstance(v, Secret)]


async def validate_secrets(
    names: List[str], api=None
) -> None:
    """fail fast when referenced secrets do not exist.

    workers with unresolvable references boot with the literal
    template string in the env var, which is a confusing runtime
    failure; checking up front turns it into a clear provision error.
    """
    if not names:
        return
    if api is None:
        from .api import AppsApiClient

        api = AppsApiClient()
    existing = {s["name"] for s in await api.list_secrets()}
    missing = sorted(set(names) - existing)
    if missing:
        raise SecretError(
            f"secret{'s' if len(missing) > 1 else ''} not found: "
            f"{', '.join(missing)}. create with `rp secret add <name>`"
        )
