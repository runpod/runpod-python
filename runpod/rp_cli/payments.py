"""create and inspect wallet payments without handling wallet signing."""

import re
import sys
from urllib.parse import quote
from uuid import uuid4

import click

from runpod import error
from runpod.api.rest import run_rest_request
from runpod.rp_cli.output import json_option, print_fields, print_json

_STATUS_MESSAGES = {
    "REQUIRES_PAYMENT": "Payment is required. Wallet credit is not available.",
    "REQUIRES_ACTION": "Action is required. Wallet credit is not available.",
    "PROCESSING": "Payment is processing. Wallet credit is not available yet.",
    "SETTLED": "Payment is settled. Wallet credit is not available yet.",
    "CREDITED": "Payment is credited. Wallet credit is available.",
    "CANCELED": "Payment is canceled. Wallet credit is not available.",
    "FAILED": "Payment failed. Wallet credit is not available.",
    "REVIEW": "Payment needs review. Contact support. Wallet credit is not available.",
}


def _payment_response(response: object) -> dict:
    if (
        not isinstance(response, dict)
        or not isinstance(response.get("id"), str)
        or not response["id"]
        or not isinstance(response.get("status"), str)
        or not response["status"]
    ):
        raise ValueError("Invalid payment response")
    return response


def _create_payment(
    amount_cents, *, rail, currency, idempotency_key, shared_payment_token
):
    body = {
        "amountCents": amount_cents,
        "currency": currency,
        "rail": rail,
        "idempotencyKey": idempotency_key,
    }
    if shared_payment_token is not None:
        body["sharedPaymentToken"] = shared_payment_token
    return _payment_response(
        run_rest_request("POST", "/v2/billing/payments", json=body, max_retries=0)
    )


def _get_payment(payment_id: str):
    if not payment_id.strip() or payment_id in (".", ".."):
        raise ValueError("Invalid payment identifier")
    return _payment_response(
        run_rest_request("GET", f"/v2/billing/payments/{quote(payment_id, safe='')}")
    )


def _amount_cents(amount: str, rail: str) -> int:
    if len(amount) > 32 or re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", amount) is None:
        raise click.BadParameter(
            "use a dollar decimal with at most two decimal places (for example, 10.01)",
            param_hint="--amount",
        )
    dollars, _, cents = amount.partition(".")
    value = int(dollars) * 100 + int(cents.ljust(2, "0"))
    maximum = 1_000_000 if rail == "STABLECOIN" else 2_500_000
    if not 1_000 <= value <= maximum:
        raise click.BadParameter(
            f"must be between $10.00 and ${maximum // 100}.00 for {rail}",
            param_hint="--amount",
        )
    return value


def _read_spt() -> str:
    if sys.stdin.isatty():
        raise click.UsageError(
            "--spt-stdin requires piped input, not an interactive terminal"
        )
    try:
        token = sys.stdin.read(515)
    except Exception:
        raise click.ClickException(
            "Could not read the shared payment token from stdin"
        ) from None
    if token.endswith("\r\n"):
        token = token[:-2]
    elif token.endswith("\n"):
        token = token[:-1]
    if len(token) > 512 or re.fullmatch(r"spt_[A-Za-z0-9]+", token) is None:
        raise click.UsageError(
            "stdin must contain one spt_[A-Za-z0-9]+ token, at most 512 characters"
        )
    return token


def _request_failure(exc: Exception, creating: bool) -> click.ClickException:
    message = "Payment request failed."
    if isinstance(exc, error.AuthenticationError):
        message = "Payment authentication failed; check your API key."
    elif isinstance(exc, error.QueryError) and type(exc.status_code) is int:
        message = f"Payment request failed (HTTP {exc.status_code})."
    if creating:
        message += (
            " If no payment id is known, retry create with the same payload and"
            " --idempotency-key set to the original retry key."
            " Otherwise, use payments get with that id."
        )
    else:
        message += " Retry payments get with the same id."
    return click.ClickException(message)


def _show(payment: dict, as_json: bool) -> None:
    if as_json:
        print_json(payment)
        return
    print_fields(
        payment,
        [
            ("id", lambda row: row["id"]),
            ("status", lambda row: row["status"]),
            ("amount (cents)", lambda row: row.get("amountCents")),
            ("currency", lambda row: row.get("currency")),
            ("rail", lambda row: row.get("rail")),
            ("created", lambda row: row.get("createdAt")),
            ("credited", lambda row: row.get("creditedAt")),
            ("balance", lambda row: row.get("balance")),
        ],
    )
    click.echo(
        _STATUS_MESSAGES.get(
            payment["status"],
            "Wallet credit is not confirmed; only CREDITED confirms it.",
        )
    )
    if payment.get("deposit"):
        click.echo("Deposit instructions:")
        print_fields(
            payment["deposit"],
            [
                ("network", lambda row: row.get("network")),
                ("address", lambda row: row.get("address")),
                ("token", lambda row: row.get("token")),
                ("token contract", lambda row: row.get("tokenContract")),
                ("amount (atomic)", lambda row: row.get("amountAtomic")),
            ],
        )
    if payment.get("nextAction"):
        click.echo("Next action:")
        print_fields(
            payment["nextAction"],
            [
                ("type", lambda row: row.get("type")),
                ("url", lambda row: row.get("url")),
            ],
        )
    if payment["status"] not in ("CREDITED", "CANCELED", "FAILED", "REVIEW"):
        click.echo("Poll payments get with this id to check its status.")


@click.group()
def payments():
    """Create and inspect wallet payments. Only CREDITED confirms wallet credit."""


@payments.command(name="create")
@click.option(
    "--amount", required=True, help="USD dollars, with at most two decimal places."
)
@click.option(
    "--rail",
    type=click.Choice(["STABLECOIN", "CARD"]),
    default="STABLECOIN",
    show_default=True,
)
@click.option(
    "--currency", type=click.Choice(["usd"]), default="usd", show_default=True
)
@click.option(
    "--idempotency-key",
    help="Reuse a retry key (1-255 characters). Generated automatically when omitted.",
)
@click.option(
    "--spt-stdin",
    is_flag=True,
    help="Read a shared payment token from piped stdin (required for CARD).",
)
@json_option
def payments_create(amount, rail, currency, idempotency_key, spt_stdin, as_json):
    """Create a payment; this does not transfer or sign with a wallet.

    A retry key is generated and written to stderr before sending the request.
    If no id is known, reuse it with --idempotency-key and the same payload.
    Once an id is known, poll payments get with that id.
    """
    amount_cents = _amount_cents(amount, rail)
    if idempotency_key is not None and (
        not idempotency_key.strip() or len(idempotency_key) > 255
    ):
        raise click.BadParameter(
            "use a nonempty key of 1 to 255 characters", param_hint="--idempotency-key"
        )
    if spt_stdin and rail != "CARD":
        raise click.UsageError("--spt-stdin requires --rail CARD")
    if rail == "CARD" and not spt_stdin:
        raise click.UsageError("--rail CARD requires --spt-stdin")
    token = _read_spt() if spt_stdin else None
    if idempotency_key is None:
        idempotency_key = str(uuid4())
        click.echo(f"Payment retry key: {idempotency_key}", err=True)
    try:
        payment = _create_payment(
            amount_cents,
            rail=rail,
            currency=currency,
            idempotency_key=idempotency_key,
            shared_payment_token=token,
        )
    except Exception as exc:
        raise _request_failure(exc, creating=True) from None
    _show(payment, as_json)


@payments.command(name="get")
@click.argument("payment_id")
@json_option
def payments_get(payment_id, as_json):
    """Retrieve a payment by PAYMENT_ID without creating another payment."""
    try:
        payment = _get_payment(payment_id)
    except Exception as exc:
        raise _request_failure(exc, creating=False) from None
    _show(payment, as_json)
