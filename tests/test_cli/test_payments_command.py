"""payment commands, exact currency parsing, and secret-safe failures."""

import io
import json
import traceback
from importlib.metadata import distribution
from unittest.mock import patch
from uuid import UUID

import click
import pytest
import requests
from click.testing import CliRunner

from runpod import error
from runpod.rp_cli import payments
from runpod.rp_cli.main import cli
from tests.test_cli.test_payments_transport import PAYMENT

CREATE = ["payments", "create", "--amount", "10.01", "--idempotency-key", "retry-key"]


@pytest.fixture(autouse=True)
def isolate_cli(monkeypatch):
    monkeypatch.setattr("runpod.rp_cli.update.start_background_check", lambda: None)
    monkeypatch.setattr("runpod.rp_cli.console.console._width", 200)


@pytest.mark.parametrize("alias", ["rp", "runpod"])
def test_entrypoints_create_and_get(alias):
    entrypoint = next(
        entry for entry in distribution("runpod").entry_points if entry.name == alias
    )
    assert entrypoint.value == "runpod.rp_cli.main:run"
    with patch.object(payments, "_create_payment", return_value=PAYMENT) as create:
        result = CliRunner().invoke(cli, [*CREATE, "--json"], prog_name=alias)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == PAYMENT
    create.assert_called_once_with(
        1001,
        rail="STABLECOIN",
        currency="usd",
        idempotency_key="retry-key",
        shared_payment_token=None,
    )
    with patch.object(payments, "_get_payment", return_value=PAYMENT) as get:
        result = CliRunner().invoke(
            cli, ["payments", "get", "payment-1", "--json"], prog_name=alias
        )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == PAYMENT
    get.assert_called_once_with("payment-1")


@pytest.mark.parametrize("rail", ["STABLECOIN", "CARD"])
def test_create_generates_retry_key_without_changing_json_output(rail):
    with patch.object(payments, "_create_payment", return_value=PAYMENT) as create:
        result = CliRunner().invoke(
            cli,
            [
                "payments",
                "create",
                "--amount",
                "25",
                "--rail",
                rail,
                "--json",
                *(["--spt-stdin"] if rail == "CARD" else []),
            ],
            input="spt_secret\n" if rail == "CARD" else None,
        )
    assert result.exit_code == 0, result.output
    key = create.call_args.kwargs["idempotency_key"]
    assert UUID(key).version == 4
    assert result.stderr == f"Payment retry key: {key}\n"
    assert json.loads(result.stdout) == PAYMENT
    assert "spt_secret" not in result.output


def test_generated_retry_key_is_available_before_an_interrupted_request():
    def interrupted(*args, **kwargs):
        stream = click.get_text_stream("stderr")
        stream.flush()
        assert kwargs["idempotency_key"] in stream.buffer.getvalue().decode()
        raise KeyboardInterrupt

    with patch.object(payments, "_create_payment", side_effect=interrupted) as create:
        result = CliRunner().invoke(cli, ["payments", "create", "--amount", "25"])
    assert result.exit_code == 1
    assert create.call_args.kwargs["idempotency_key"] in result.stderr


def test_generated_key_can_replay_an_ambiguous_request():
    with patch.object(
        payments,
        "_create_payment",
        side_effect=[requests.Timeout("spt_secret"), PAYMENT],
    ) as create:
        first = CliRunner().invoke(cli, ["payments", "create", "--amount", "25"])
        assert first.exit_code == 1
        key = create.call_args.kwargs["idempotency_key"]
        assert key in first.stderr
        assert "--idempotency-key" in first.output
        assert "spt_secret" not in first.output
        retry = CliRunner().invoke(
            cli, ["payments", "create", "--amount", "25", "--idempotency-key", key]
        )
    assert retry.exit_code == 0
    assert create.call_args_list[0] == create.call_args_list[1]


def test_separate_create_commands_generate_distinct_keys():
    with patch.object(payments, "_create_payment", return_value=PAYMENT) as create:
        for _ in range(2):
            result = CliRunner().invoke(cli, ["payments", "create", "--amount", "25"])
            assert result.exit_code == 0
    assert (
        create.call_args_list[0].kwargs["idempotency_key"]
        != create.call_args_list[1].kwargs["idempotency_key"]
    )


@pytest.mark.parametrize(
    "amount, rail, cents",
    [
        ("10", "STABLECOIN", 1000),
        ("10.1", "STABLECOIN", 1010),
        ("19.99", "STABLECOIN", 1999),
        ("10000.00", "STABLECOIN", 1000000),
        ("25000", "CARD", 2500000),
        ("00010.01", "CARD", 1001),
    ],
)
def test_exact_cents(amount, rail, cents):
    with patch.object(payments, "_create_payment", return_value=PAYMENT) as create:
        result = CliRunner().invoke(
            cli,
            [
                *CREATE,
                "--amount",
                amount,
                "--rail",
                rail,
                *(["--spt-stdin"] if rail == "CARD" else []),
            ],
            input="spt_test123\n" if rail == "CARD" else None,
        )
    assert result.exit_code == 0, result.output
    assert create.call_args.args == (cents,)


@pytest.mark.parametrize(
    "amount, rail",
    [
        ("9.99", "STABLECOIN"),
        ("10000.01", "STABLECOIN"),
        ("25000.01", "CARD"),
        ("10.001", "CARD"),
        ("10.000", "CARD"),
        ("NaN", "CARD"),
        ("Infinity", "CARD"),
        ("1e2", "CARD"),
        (" 10", "CARD"),
        ("+10", "CARD"),
        ("-10", "CARD"),
        ("10.", "CARD"),
        ("１０", "CARD"),
        ("1" * 1000, "CARD"),
    ],
)
def test_invalid_amount_never_sends(amount, rail):
    with patch.object(payments, "_create_payment") as create:
        result = CliRunner().invoke(cli, [*CREATE, "--amount", amount, "--rail", rail])
    assert result.exit_code == 2
    assert "--amount" in result.output
    create.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        [*CREATE, "--idempotency-key", ""],
        [*CREATE, "--idempotency-key", " "],
        [*CREATE, "--idempotency-key", "k" * 256],
        [*CREATE, "--rail", "stablecoin"],
        [*CREATE, "--currency", "USD"],
        [*CREATE, "--spt-stdin"],
        [*CREATE, "--rail", "CARD"],
    ],
)
def test_invalid_flags_never_send(arguments):
    with patch.object(payments, "_create_payment") as create:
        result = CliRunner().invoke(cli, arguments)
    assert result.exit_code == 2
    create.assert_not_called()


@pytest.mark.parametrize("suffix", ["", "\n", "\r\n"])
def test_stdin_token(suffix):
    token = "spt_" + "a" * 508
    with patch.object(payments, "_create_payment", return_value=PAYMENT) as create:
        result = CliRunner().invoke(
            cli,
            [*CREATE, "--rail", "CARD", "--spt-stdin", "--json"],
            input=token + suffix,
        )
    assert result.exit_code == 0, result.output
    assert create.call_args.kwargs["shared_payment_token"] == token
    assert token not in result.output
    assert json.loads(result.output) == PAYMENT


@pytest.mark.parametrize(
    "token",
    [
        "",
        "spt_",
        "spt_secret-123",
        " spt_secret",
        "spt_secret ",
        "spt_secret\n\n",
        "spt_secret\nother",
        "spt_" + "a" * 509,
        "spt_" + "a" * 10000,
    ],
)
def test_invalid_stdin_never_echoes_or_sends(token):
    with patch.object(payments, "_create_payment") as create:
        result = CliRunner().invoke(
            cli, [*CREATE, "--rail", "CARD", "--spt-stdin"], input=token
        )
    assert result.exit_code == 2
    assert "stdin must contain" in result.output
    assert "spt_secret" not in result.output
    create.assert_not_called()


def test_stdin_read_is_bounded(monkeypatch):
    class BoundedInput(io.StringIO):
        def read(self, size=-1):
            assert size == 515
            return super().read(size)

    monkeypatch.setattr(payments.sys, "stdin", BoundedInput("spt_" + "a" * 10000))
    with pytest.raises(click.UsageError):
        payments._read_spt()


def test_interactive_stdin_rejected(monkeypatch):
    stream = io.StringIO("spt_secret")
    monkeypatch.setattr(stream, "isatty", lambda: True)
    monkeypatch.setattr(payments.sys, "stdin", stream)
    with pytest.raises(click.UsageError, match="piped input"):
        payments._read_spt()


def test_stdin_read_failure_is_safe(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(
        stream, "read", lambda size: (_ for _ in ()).throw(OSError("spt_secret"))
    )
    monkeypatch.setattr(payments.sys, "stdin", stream)
    with pytest.raises(click.ClickException) as raised:
        payments._read_spt()
    assert "spt_secret" not in "".join(traceback.format_exception(raised.value))


def test_spt_environment_is_not_consumed(monkeypatch):
    monkeypatch.setenv("RUNPOD_SHARED_PAYMENT_TOKEN", "spt_secret")
    monkeypatch.setenv("RUNPOD_SPT", "spt_secret")
    with patch.object(payments, "_create_payment", return_value=PAYMENT) as create:
        result = CliRunner().invoke(cli, [*CREATE, "--rail", "CARD"])
    assert result.exit_code == 2
    assert "--rail CARD requires --spt-stdin" in result.output
    create.assert_not_called()


@pytest.mark.parametrize("flag", ["--shared-payment-token", "--spt"])
def test_no_argv_token_option(flag):
    result = CliRunner().invoke(cli, [*CREATE, "--rail", "CARD", flag, "spt_secret"])
    assert result.exit_code == 2
    assert "No such option" in result.output


@pytest.mark.parametrize(
    "status, phrase",
    [
        ("REQUIRES_PAYMENT", "Payment is required"),
        ("REQUIRES_ACTION", "Action is required"),
        ("PROCESSING", "Payment is processing"),
        ("SETTLED", "Payment is settled"),
        ("CREDITED", "Wallet credit is available."),
        ("CANCELED", "Payment is canceled"),
        ("FAILED", "Payment failed"),
        ("REVIEW", "Payment needs review"),
        ("FUTURE_STATUS", "Wallet credit is not confirmed"),
    ],
)
@pytest.mark.parametrize("command", ["create", "get"])
@pytest.mark.parametrize("as_json", [False, True])
def test_status_output(status, phrase, command, as_json):
    response = dict(PAYMENT, status=status)
    arguments = CREATE if command == "create" else ["payments", "get", "payment-1"]
    with patch.object(payments, f"_{command}_payment", return_value=response):
        result = CliRunner().invoke(cli, [*arguments, *(["--json"] if as_json else [])])
    assert result.exit_code == 0, result.output
    if as_json:
        assert json.loads(result.output) == response
        return
    assert status in result.output
    assert phrase in result.output
    if status != "CREDITED":
        assert "Wallet credit is available." not in result.output
    if status == "REVIEW":
        assert "Contact support" in result.output
        assert "Poll payments get" not in result.output
    for value in PAYMENT["deposit"].values():
        assert value in result.output


@pytest.mark.parametrize(
    "action",
    [
        {"type": "REDIRECT_TO_URL", "url": "https://example.com/checkout"},
        {"type": "SPT_ISSUER", "url": None},
        {"type": "FUTURE_ACTION", "url": "https://example.com/action"},
    ],
)
@pytest.mark.parametrize("as_json", [False, True])
def test_next_action_and_nullable_fields(action, as_json):
    response = dict(
        PAYMENT, rail="CARD", status="REQUIRES_ACTION", deposit=None, nextAction=action
    )
    with patch.object(payments, "_get_payment", return_value=response):
        result = CliRunner().invoke(
            cli, ["payments", "get", "payment-1", *(["--json"] if as_json else [])]
        )
    assert result.exit_code == 0, result.output
    if as_json:
        assert json.loads(result.output) == response
    else:
        assert action["type"] in result.output
        assert "Deposit instructions" not in result.output
        if action["url"]:
            assert action["url"] in result.output


@pytest.mark.parametrize("command", ["create", "get"])
@pytest.mark.parametrize(
    "exc",
    [
        error.QueryError("spt_secret", "spt_secret", 400, ["spt_secret"]),
        error.RateLimitError("spt_secret", "spt_secret", 1),
        error.AuthenticationError("spt_secret"),
        requests.RequestException("spt_secret"),
        ValueError("spt_secret"),
        RuntimeError("spt_secret"),
    ],
)
def test_error_messages_and_tracebacks_do_not_reflect_secrets(command, exc):
    arguments = (
        [*CREATE, "--rail", "CARD", "--spt-stdin"]
        if command == "create"
        else ["payments", "get", "payment-1"]
    )
    with patch.object(payments, f"_{command}_payment", side_effect=exc):
        result = CliRunner().invoke(cli, arguments, input="spt_secret\n")
    assert result.exit_code == 1
    assert "spt_secret" not in result.output
    assert "spt_secret" not in "".join(traceback.format_exception(result.exception))
    assert "payments get" in result.output


def test_upstream_problem_details_are_not_reflected(monkeypatch, caplog):
    monkeypatch.setattr("runpod.api_key", "key")
    monkeypatch.setenv("RUNPOD_API_BASE_URL", "https://payments.invalid")
    response = requests.Response()
    response.status_code = 422
    response._content = b'{"detail":"spt_secret", "errors":["spt_secret"]}'
    with patch("runpod.api.rest.requests.request", return_value=response) as send:
        result = CliRunner().invoke(
            cli,
            [*CREATE, "--rail", "CARD", "--spt-stdin", "--json"],
            input="spt_secret\n",
        )
    assert result.exit_code == 1
    assert "HTTP 422" in result.output
    assert "same payload" in result.output
    assert "spt_secret" not in result.output + caplog.text
    send.assert_called_once()


def test_replay_keeps_the_same_key_and_payload():
    with patch.object(payments, "_create_payment", return_value=PAYMENT) as create:
        for _ in range(2):
            result = CliRunner().invoke(cli, CREATE)
            assert result.exit_code == 0
    assert create.call_args_list[0] == create.call_args_list[1]


@pytest.mark.parametrize("command", ["create", "get"])
def test_exception_chain_is_suppressed(command):
    arguments = (
        [*CREATE, "--rail", "CARD", "--spt-stdin"]
        if command == "create"
        else ["payments", "get", "payment-1"]
    )
    with patch.object(
        payments, f"_{command}_payment", side_effect=RuntimeError("spt_secret")
    ):
        result = CliRunner().invoke(
            cli, arguments, input="spt_secret\n", standalone_mode=False
        )
    assert isinstance(result.exception, click.ClickException)
    assert result.exception.__cause__ is None
    assert result.exception.__suppress_context__
    assert "spt_secret" not in "".join(traceback.format_exception(result.exception))


@pytest.mark.parametrize("name", ["fund", "funding", "x402"])
def test_no_alternative_names(name):
    result = CliRunner().invoke(cli, [name])
    assert result.exit_code == 2
    assert "No such command" in result.output
