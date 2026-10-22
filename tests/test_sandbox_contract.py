"""Sandbox requests and snapshots use the current REST contract."""

import asyncio

import pytest

from runpod import AsyncioSandbox, Sandbox
from runpod.sandbox import SandboxInfo
from tests.test_sandbox import SandboxService, sandbox_peer


@pytest.mark.asyncio
@pytest.mark.parametrize("async_api", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    "options, expected_body",
    [
        ({}, {}),
        ({"image": "python:3.12-slim"}, {"image": "python:3.12-slim"}),
        ({"template_id": "template"}, {"templateId": "template"}),
        (
            {"cpu_flavor_id": "cpu3c", "vcpu_count": 2},
            {"cpu": {"id": "cpu3c", "vcpuCount": 2}},
        ),
        (
            {"image": "image", "cpu_flavor_id": "cpu3c", "vcpu_count": 2},
            {"image": "image", "cpu": {"id": "cpu3c", "vcpuCount": 2}},
        ),
    ],
)
async def test_create_wire_contract(async_api, options, expected_body, monkeypatch):
    with sandbox_peer() as peer:
        create_record = peer.create_record

        def modern_record(body):
            record = create_record(body)
            record.pop("imageName")
            record.pop("cpuFlavorId")
            record["image"] = body.get("image", "default-image")
            record["cpu"] = {
                "id": "cpu3c",
                "vcpuCount": 2,
                "memory": 4,
            }
            return record

        monkeypatch.setattr(peer, "create_record", modern_record)
        if async_api:
            async with AsyncioSandbox(**options, **peer.options) as sandbox:
                info = sandbox.info
        else:

            def create_sync():
                with Sandbox(**options, **peer.options) as sandbox:
                    return sandbox.info

            info = await asyncio.to_thread(create_sync)

        assert peer.requests[0][:3] == ("POST", "/v2/sandboxes", expected_body)
        assert info.image == options.get("image", "default-image")
        assert info.cpu_flavor_id == "cpu3c"
        assert peer.records[info.id]["state"] == "TERMINATED"


@pytest.mark.parametrize("api", [Sandbox, AsyncioSandbox])
def test_image_and_template_remain_mutually_exclusive(api):
    with pytest.raises(ValueError, match="at most one"):
        api(image="image", template_id="template")


@pytest.mark.parametrize("api", [Sandbox, AsyncioSandbox])
@pytest.mark.parametrize("options", [{"cpu_flavor_id": "cpu3c"}, {"vcpu_count": 2}])
def test_partial_cpu_selection_is_rejected_before_any_request(api, options):
    with sandbox_peer() as peer:
        with pytest.raises(ValueError, match="cpu_flavor_id and vcpu_count together"):
            api(**options, **peer.options)
        assert peer.requests == []


@pytest.mark.parametrize("api", [Sandbox, AsyncioSandbox])
def test_explicit_memory_constraint_is_not_a_create_option(api):
    with sandbox_peer() as peer:
        with pytest.raises(TypeError, match="memory_in_gb"):
            api(memory_in_gb=4, **peer.options)
        assert peer.requests == []


@pytest.mark.parametrize(
    "fields, expected_image, expected_cpu",
    [
        ({"image": "image", "cpu": {"id": "cpu3c"}}, "image", "cpu3c"),
        ({"imageName": "legacy", "cpuFlavorId": "cpu5c"}, None, None),
        (
            {
                "image": "image",
                "imageName": "legacy",
                "cpu": {"id": "cpu3c"},
                "cpuFlavorId": "cpu5c",
            },
            "image",
            "cpu3c",
        ),
        ({"image": None, "imageName": "legacy"}, None, None),
        ({"cpu": None, "cpuFlavorId": "cpu5c"}, None, None),
        ({}, None, None),
    ],
)
def test_snapshot_reads_only_current_fields(fields, expected_image, expected_cpu):
    record = SandboxService().create_record({})
    record.pop("imageName")
    record.pop("cpuFlavorId")
    record.update(fields)
    info = SandboxInfo.from_dict(record)
    assert info.image == expected_image
    assert info.cpu_flavor_id == expected_cpu
