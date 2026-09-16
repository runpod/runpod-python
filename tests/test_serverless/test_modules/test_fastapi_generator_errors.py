"""Regression tests for generator failures in the development API."""

import httpx
import pytest

from runpod.serverless.modules import rp_fastapi


@pytest.mark.parametrize("endpoint", ["runsync", "status", "stream"])
@pytest.mark.parametrize("async_handler", [False, True])
@pytest.mark.parametrize("yield_first", [False, True])
async def test_generator_failure_returns_failed_job(
    monkeypatch, endpoint, async_handler, yield_first
):
    """Handler failures remain job errors even after a partial result was produced."""

    def sync_generator(job):
        if yield_first:
            yield "partial result"
        raise ValueError("stream failed")

    async def async_generator(job):
        if yield_first:
            yield "partial result"
        raise ValueError("stream failed")

    monkeypatch.setattr(rp_fastapi.Heartbeat, "start_ping", lambda self, mirror: None)
    monkeypatch.setattr(rp_fastapi, "job_list", rp_fastapi.JobsProgress())
    worker = rp_fastapi.WorkerAPI(
        {"handler": async_generator if async_handler else sync_generator}
    )
    transport = httpx.ASGITransport(app=worker.rp_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        if endpoint == "runsync":
            response = await client.post("/runsync", json={"input": {}})
        else:
            job = (await client.post("/run", json={"input": {}})).json()
            response = await client.post(f"/{endpoint}/{job['id']}")
            assert rp_fastapi.job_list.get(job["id"]) is None

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "FAILED"
    assert "stream failed" in result["error"]
    assert "output" not in result
