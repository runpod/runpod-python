"""Request envelopes must retain their payload and endpoint options."""

import copy
import json

import pytest
import requests

import runpod


class RecordingAdapter(requests.adapters.BaseAdapter):
    """Record the prepared request without contacting an endpoint."""

    def __init__(self):
        self.requests = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        response = requests.Response()
        response.status_code = 200
        response._content = (
            b'{"id":"job-test","status":"COMPLETED","output":{"accepted":true}}'
        )
        response.headers["Content-Type"] = "application/json"
        response.request = request
        return response

    def close(self):
        pass


@pytest.mark.parametrize("method", ["run", "run_sync"])
@pytest.mark.parametrize(
    "payload,expected",
    [
        pytest.param({}, {"input": {}}, id="bare-empty"),
        pytest.param({"prompt": "hello"}, {"input": {"prompt": "hello"}}, id="bare"),
        pytest.param(
            {
                "input": {},
                "webhook": "https://callback.invalid",
                "policy": {"ttl": 60000},
            },
            {
                "input": {},
                "webhook": "https://callback.invalid",
                "policy": {"ttl": 60000},
            },
            id="wrapped-empty",
        ),
        pytest.param(
            {"input": {"prompt": "hello"}, "webhook": "https://callback.invalid"},
            {"input": {"prompt": "hello"}, "webhook": "https://callback.invalid"},
            id="wrapped",
        ),
    ],
)
def test_request_envelope(method, payload, expected):
    """Both submission methods preserve wrapped input and bare-input convenience."""
    original = copy.deepcopy(payload)
    endpoint = runpod.Endpoint("endpoint-test", api_key="test-key")
    endpoint.rp_client.rp_session.trust_env = False
    adapter = RecordingAdapter()
    endpoint.rp_client.rp_session.mount("https://", adapter)
    try:
        result = getattr(endpoint, method)(payload)
    finally:
        endpoint.rp_client.rp_session.close()

    assert len(adapter.requests) == 1
    request = adapter.requests[0]
    assert request.method == "POST"
    assert request.headers["Authorization"] == "Bearer test-key"
    assert (
        request.url
        == f"https://api.runpod.ai/v2/endpoint-test/{method.replace('_', '')}"
    )
    assert json.loads(request.body) == expected
    assert payload == original
    if method == "run":
        assert result.job_id == "job-test"
    else:
        assert result == {"accepted": True}
