"""private payment transport and the cli-only package boundary."""

import importlib.util
import json
from unittest.mock import patch

import pytest

import runpod
from runpod.error import AuthenticationError, QueryError
from runpod.rp_cli import payments
from tests.test_api.test_rest import rest_server as rest_server

PAYMENT = {
    "id": "payment-1",
    "amountCents": 1001,
    "currency": "usd",
    "rail": "STABLECOIN",
    "status": "REQUIRES_PAYMENT",
    "createdAt": "2026-11-04T12:00:00Z",
    "creditedAt": None,
    "balance": None,
    "deposit": {
        "network": "base",
        "address": "0x123",
        "token": "usdc",
        "tokenContract": "0x456",
        "amountAtomic": "10010000",
    },
    "nextAction": None,
    "futureField": {"nested": None},
}


def create(**kwargs):
    return payments._create_payment(
        1001,
        **{
            "currency": "usd",
            "rail": "STABLECOIN",
            "idempotency_key": "retry",
            "shared_payment_token": None,
            **kwargs,
        },
    )


def test_payments_are_not_public_sdk_methods():
    for name in (
        "create_payment",
        "get_payment",
        "create_payment_async",
        "get_payment_async",
    ):
        assert name not in runpod.__all__
        assert not hasattr(runpod, name)
    assert importlib.util.find_spec("runpod.api.payments") is None


@pytest.mark.parametrize("rail, token", [("STABLECOIN", None), ("CARD", "spt_test123")])
def test_create_payload_and_explicit_replay(rail, token):
    with patch.object(payments, "run_rest_request", return_value=PAYMENT) as send:
        for _ in range(2):
            assert create(rail=rail, shared_payment_token=token) is PAYMENT
    assert send.call_count == 2
    assert send.call_args_list[0] == send.call_args_list[1]
    expected = dict(amountCents=1001, currency="usd", rail=rail, idempotencyKey="retry")
    if token:
        expected["sharedPaymentToken"] = token
    send.assert_called_with(
        "POST", "/v2/billing/payments", json=expected, max_retries=0
    )


@pytest.mark.parametrize("payment_id", ["", " ", ".", ".."])
def test_invalid_id_never_sends(payment_id):
    with patch.object(payments, "run_rest_request") as send:
        with pytest.raises(ValueError):
            payments._get_payment(payment_id)
    send.assert_not_called()


def test_get_encodes_path_segment():
    with patch.object(payments, "run_rest_request", return_value=PAYMENT) as send:
        assert payments._get_payment("payment/a?b#c") is PAYMENT
    send.assert_called_once_with("GET", "/v2/billing/payments/payment%2Fa%3Fb%23c")


@pytest.mark.parametrize(
    "status",
    [
        "REQUIRES_PAYMENT",
        "REQUIRES_ACTION",
        "PROCESSING",
        "SETTLED",
        "CREDITED",
        "CANCELED",
        "FAILED",
        "REVIEW",
        "FUTURE_STATUS",
    ],
)
def test_all_statuses_and_unknown_fields_are_preserved(status):
    response = dict(PAYMENT, status=status)
    with patch.object(payments, "run_rest_request", return_value=response):
        assert create() is response
        assert payments._get_payment("payment-1") is response


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        "bad",
        {},
        {"id": "x"},
        {"id": "", "status": "PROCESSING"},
        {"id": "x", "status": None},
        {"id": 1, "status": "PROCESSING"},
    ],
)
def test_malformed_responses_are_errors(response):
    with patch.object(payments, "run_rest_request", return_value=response):
        with pytest.raises(ValueError, match="Invalid payment response"):
            create()
        with pytest.raises(ValueError, match="Invalid payment response"):
            payments._get_payment("payment-1")


def test_real_transport_roundtrip(rest_server, monkeypatch):
    monkeypatch.setattr(runpod, "api_key", "key")
    rest_server.update(
        path="/prefix/v2/billing/payments",
        body=json.dumps(PAYMENT).encode(),
        expected_body={
            "amountCents": 1001,
            "currency": "usd",
            "rail": "STABLECOIN",
            "idempotencyKey": "retry",
        },
    )
    assert create() == PAYMENT
    assert create() == PAYMENT
    assert rest_server["hits"] == 2
    del rest_server["expected_body"]
    rest_server["path"] = "/prefix/v2/billing/payments/payment-1"
    assert payments._get_payment("payment-1") == PAYMENT
    assert rest_server["hits"] == 3


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 409, 422, 429, 500])
def test_create_http_errors_are_not_retried(rest_server, monkeypatch, status):
    monkeypatch.setattr(runpod, "api_key", "key")
    rest_server.update(
        path="/prefix/v2/billing/payments",
        status=status,
        body=(
            b'{"title":"Rejected", "detail":"Payment unavailable", '
            b'"errors":["invalid"]}'
        ),
        content_type="application/problem+json",
    )
    expected = AuthenticationError if status == 401 else QueryError
    with pytest.raises(expected) as raised:
        create()
    if status != 401:
        assert raised.value.status_code == status
        assert raised.value.errors == ["invalid"]
    assert rest_server["hits"] == 1
