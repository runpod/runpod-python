"""shared api key selection for the sdk and cli."""

import os


def load_api_key(profile: str = "default"):
    """load the environment key or the selected credential profile."""
    key = os.environ.get("RUNPOD_API_KEY")
    if key:
        return key
    try:
        from .credentials import get_credentials

        credentials = get_credentials(profile)
    except Exception:
        return None
    return credentials.get("api_key") if credentials else None


def resolve_api_key(api_key=None) -> str:
    """select an explicit key or the sdk's configured key."""
    import runpod
    from runpod.error import AuthenticationError

    key = api_key or runpod.api_key
    if not key:
        raise AuthenticationError("No API key provided")
    return key
