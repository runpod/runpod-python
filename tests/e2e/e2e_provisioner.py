"""Provision real Runpod serverless endpoints for e2e testing.

Reads tests.json, groups by hardwareConfig, provisions one endpoint per
unique config using Flash's Endpoint(image=...) mode. Injects the PR's
runpod-python via PodTemplate(dockerArgs=...) so the remote worker runs
the branch under test.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Force Flash to use ServerlessEndpoint (deploy mode) instead of LiveServerless.
# LiveServerless forcefully overwrites imageName with Flash's base image,
# ignoring the mock-worker image we need to deploy.
os.environ["FLASH_IS_LIVE_PROVISIONING"] = "false"

from runpod_flash import Endpoint, GpuGroup, PodTemplate  # noqa: E402

MOCK_WORKER_IMAGE = "runpod/mock-worker:latest"
DEFAULT_CMD = "python -u /handler.py"
TESTS_JSON = Path(__file__).parent / "tests.json"

# Map gpuIds strings from tests.json to GpuGroup enum values
_GPU_MAP: dict[str, GpuGroup] = {g.value: g for g in GpuGroup}


def _build_docker_args(base_docker_args: str, git_ref: str | None) -> str:
    """Build dockerArgs that injects PR runpod-python before the original CMD.

    If git_ref is set, prepends pip install. If base_docker_args is provided
    (e.g., for generator handlers), uses that as the CMD instead of default.
    """
    cmd = base_docker_args or DEFAULT_CMD
    if not git_ref:
        return cmd

    install_url = f"git+https://github.com/runpod/runpod-python@{git_ref}"
    return (
        '/bin/bash -c "'
        "apt-get update && apt-get install -y git && "
        f"pip install {install_url} --no-cache-dir && "
        f'{cmd}"'
    )


def _parse_gpu_ids(gpu_ids_str: str) -> list[GpuGroup]:
    """Parse comma-separated GPU ID strings into GpuGroup enums."""
    result = []
    for g in gpu_ids_str.split(","):
        g = g.strip()
        if g in _GPU_MAP:
            result.append(_GPU_MAP[g])
    if not result:
        result.append(GpuGroup.ANY)
    return result


def load_test_cases() -> list[dict[str, Any]]:
    """Load test cases from tests.json."""
    return json.loads(TESTS_JSON.read_text())


def hardware_config_key(hw: dict) -> str:
    """Stable string key for grouping tests by hardware config.

    Excludes endpoint name so tests with identical GPU and template
    settings share a single provisioned endpoint.
    """
    normalized = {
        "gpuIds": hw.get("endpointConfig", {}).get("gpuIds", ""),
        "dockerArgs": hw.get("templateConfig", {}).get("dockerArgs", ""),
    }
    return json.dumps(normalized, sort_keys=True)


def provision_endpoints(
    test_cases: list[dict[str, Any]],
) -> dict[str, Endpoint]:
    """Provision one Endpoint per unique hardwareConfig.

    Returns a dict mapping hardwareConfig key -> provisioned Endpoint.
    The Endpoint is in image mode (not yet deployed). Deployment happens
    on first .run() or .runsync() call.

    Args:
        test_cases: List of test case dicts from tests.json.

    Returns:
        Dict of hardware_key -> Endpoint instance.
    """
    git_ref = os.environ.get("RUNPOD_SDK_GIT_REF")
    log.info("RUNPOD_SDK_GIT_REF=%s", git_ref or "(not set)")
    log.info("FLASH_IS_LIVE_PROVISIONING=%s", os.environ.get("FLASH_IS_LIVE_PROVISIONING"))
    log.info("Loading %d test cases from %s", len(test_cases), TESTS_JSON)
    seen: dict[str, Endpoint] = {}

    for tc in test_cases:
        hw = tc["hardwareConfig"]
        key = hardware_config_key(hw)
        if key in seen:
            continue

        endpoint_config = hw.get("endpointConfig", {})
        template_config = hw.get("templateConfig", {})

        base_docker_args = template_config.get("dockerArgs", "")
        docker_args = _build_docker_args(base_docker_args, git_ref)

        gpu_ids = endpoint_config.get("gpuIds", "ADA_24")
        gpus = _parse_gpu_ids(gpu_ids)

        ep_name = endpoint_config.get("name", f"rp-python-e2e-{len(seen)}")
        log.info(
            "Provisioning endpoint: name=%s image=%s gpus=%s dockerArgs=%s",
            ep_name, MOCK_WORKER_IMAGE, [g.value for g in gpus], docker_args,
        )
        ep = Endpoint(
            name=ep_name,
            image=MOCK_WORKER_IMAGE,
            gpu=gpus,
            template=PodTemplate(dockerArgs=docker_args),
            workers=(0, 1),
            idle_timeout=5,
        )
        seen[key] = ep

    log.info("Provisioned %d unique endpoints", len(seen))
    return seen
