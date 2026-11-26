"""HTTP behavior of the local worker's status endpoint."""

import json

import pytest
from fastapi.testclient import TestClient

from runpod.serverless.modules import rp_fastapi


@pytest.fixture
def make_client(monkeypatch):
    """Use the real router and job tracker without starting a heartbeat."""
    monkeypatch.setattr(rp_fastapi.Heartbeat, "start_ping", lambda self, mirror: None)
    monkeypatch.setattr(rp_fastapi.rp_capture, "install", lambda **kwargs: None)
    monkeypatch.setattr(rp_fastapi, "RUNPOD_ENDPOINT_ID", None)
    monkeypatch.setattr(rp_fastapi.JobsProgress, "_instance", None)
    monkeypatch.setattr(rp_fastapi, "job_list", rp_fastapi.JobsProgress())
    clients = []

    def create(handler):
        worker = rp_fastapi.WorkerAPI({"handler": handler})
        client = TestClient(worker.rp_app)
        clients.append(client)
        return client

    yield create

    for client in clients:
        client.close()


def submit_job(client):
    response = client.post("/run", json={"input": {"value": 42}})
    assert response.status_code == 200
    return response.json()["id"]


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("output", [{"value": 42}, None, {}])
def test_status_returns_handler_output(make_client, method, output):
    def handler(job):
        assert job["input"] == {"value": 42}
        return output

    client = make_client(handler)
    job_id = submit_job(client)
    response = client.request(method, f"/status/{job_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == job_id
    assert payload["status"] == "COMPLETED"
    assert payload["output"] == output


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_status_returns_generator_output(make_client, method):
    def handler(job):
        yield job["input"]["value"]
        yield "done"

    client = make_client(handler)
    job_id = submit_job(client)
    response = client.request(method, f"/status/{job_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == job_id
    assert payload["status"] == "COMPLETED"
    assert payload["output"] == [42, "done"]


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_status_returns_handler_error(make_client, method):
    def handler(job):
        raise ValueError("handler failed")

    client = make_client(handler)
    job_id = submit_job(client)
    response = client.request(method, f"/status/{job_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == job_id
    assert payload["status"] == "FAILED"
    assert json.loads(payload["error"])["error_message"] == "handler failed"


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_status_reports_unknown_job(make_client, method):
    def handler(job):
        pytest.fail("An unknown job must not invoke the handler")

    response = make_client(handler).request(method, "/status/not-a-job")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "not-a-job"
    assert payload["status"] == "FAILED"
    assert payload["error"] == "Job ID not found"


def test_status_openapi_operations_are_distinct(make_client):
    client = make_client(lambda job: None)
    response = client.get("/openapi.json")

    assert response.status_code == 200
    operations = response.json()["paths"]["/status/{job_id}"]
    assert {"get", "post"} <= operations.keys()
    assert operations["get"]["operationId"] != operations["post"]["operationId"]
