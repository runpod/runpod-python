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
    cred_dir = os.path.dirname(CREDENTIAL_FILE)
    os.makedirs(cred_dir, exist_ok=True)
    Path(CREDENTIAL_FILE).touch(exist_ok=True)

    with open(CREDENTIAL_FILE, "r", encoding="UTF-8") as cred_file:
        try:
            content = cred_file.read()
            config = (
                tomlkit.parse(content)
                if content.strip()
                else tomlkit.document()
            )
        except tomlkit.exceptions.ParseError as exc:
            raise ValueError("~/.runpod/config.toml is not a valid TOML file.") from exc

    if not overwrite:
        if profile in config:
            raise ValueError(
                "Profile already exists. Use set_credentials(overwrite=True) to update."
            )

    config[profile] = {"api_key": api_key}

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
