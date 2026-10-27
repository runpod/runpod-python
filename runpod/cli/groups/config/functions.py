"""
runpod | cli | config.py

A collection of functions to set and validate configurations.
Configurations are TOML files located under ~/.runpod/
"""

import os
import tempfile
from pathlib import Path

import tomli as toml
import tomlkit

CREDENTIAL_FILE = os.path.expanduser("~/.runpod/config.toml")


def set_credentials(api_key: str, profile: str = "default", overwrite=False) -> None:
    """
    Sets the user's credentials in ~/.runpod/config.toml
    If profile already exists user must pass overwrite=True.

    Args:
        api_key (str): The user's API key.
        profile (str): The profile to set the credentials for.

    --- File Structure ---

    [default]
    api_key = "RUNPOD_API_KEY"
    """
    config = _load_config()
    if not overwrite and profile in config:
        raise ValueError(
            "Profile already exists. Use set_credentials(overwrite=True) to update."
        )
    _set_profile_values(config, profile, {"api_key": api_key})
    _write_config(config)


def set_s3_keys(access_key: str, secret_key: str, profile: str = "default") -> None:
    """
    Saves S3 API keys for network volume transfers in ~/.runpod/config.toml,
    next to the profile's api_key.

    --- File Structure ---

    [default]
    api_key = "RUNPOD_API_KEY"
    s3_access_key = "..."
    s3_secret_key = "..."
    """
    config = _load_config()
    _set_profile_values(
        config, profile, {"s3_access_key": access_key, "s3_secret_key": secret_key}
    )
    _write_config(config)


def _load_config() -> tomlkit.TOMLDocument:
    """the config file as an editable document, created empty if missing."""
    os.makedirs(os.path.dirname(CREDENTIAL_FILE), exist_ok=True)
    Path(CREDENTIAL_FILE).touch(exist_ok=True)
    with open(CREDENTIAL_FILE, "r", encoding="UTF-8") as cred_file:
        content = cred_file.read()
    try:
        return tomlkit.parse(content) if content.strip() else tomlkit.document()
    except tomlkit.exceptions.ParseError as exc:
        raise ValueError("~/.runpod/config.toml is not a valid TOML file.") from exc


def _set_profile_values(config: tomlkit.TOMLDocument, profile: str, values: dict) -> None:
    """set values in a profile, keeping its other keys."""
    if profile in config:
        config[profile].update(values)
    else:
        config[profile] = values


def _write_config(config: tomlkit.TOMLDocument) -> None:
    """replace the config file atomically; mkstemp keeps it owner-only."""
    cred_dir = os.path.dirname(CREDENTIAL_FILE)
    fd, tmp_path = tempfile.mkstemp(dir=cred_dir, suffix=".toml")
    try:
        with os.fdopen(fd, "w", encoding="UTF-8") as tmp_file:
            tomlkit.dump(config, tmp_file)
        os.replace(tmp_path, CREDENTIAL_FILE)
    except BaseException:
        os.unlink(tmp_path)
        raise


def check_credentials(profile: str = "default"):
    """
    Checks if the credentials file exists and is valid.
    """
    if not os.path.exists(CREDENTIAL_FILE):
        return False, "~/.runpod/config.toml does not exist."

    # Check for default api_key
    try:
        with open(CREDENTIAL_FILE, "rb") as cred_file:
            config = toml.load(cred_file)

        if profile not in config:
            return False, f"~/.runpod/config.toml is missing {profile} profile."

        if "api_key" not in config[profile]:
            return (
                False,
                f"~/.runpod/config.toml is missing api_key for {profile} profile.",
            )

    except (TypeError, ValueError):
        return False, "~/.runpod/config.toml is not a valid TOML file."

    return True, None


def get_credentials(profile="default"):
    """
    Returns the credentials for the specified profile from ~/.runpod/config.toml

    Returns None if the file does not exist, is not valid TOML, or does not
    contain the requested profile.
    """
    if not os.path.exists(CREDENTIAL_FILE):
        return None

    try:
        with open(CREDENTIAL_FILE, "rb") as cred_file:
            credentials = toml.load(cred_file)
    except (TypeError, ValueError):
        return None

    if profile not in credentials:
        return None

    return credentials[profile]
