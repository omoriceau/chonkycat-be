"""
payments/models.py

Typed event payloads the Lambda accepts.
Validated on entry — bad payloads are rejected before touching any provider.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

EventType = Literal["charge", "refund"]


@dataclass
class ChargeEventPayload:
    order_id:       int
    amount:         Decimal
    currency:       str
    customer_email: str
    provider:       str = "stripe"
    description:    str = ""
    metadata:       dict = field(default_factory=dict)


@dataclass
class RefundEventPayload:
    payment_id:              int
    provider_transaction_id: str
    amount:                  Decimal
    provider:                str = "stripe"
    reason:                  str = ""


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

class ValidationError(Exception):
    pass


def _require(d: dict, key: str, label: str) -> Any:
    val = d.get(key)
    if val is None:
        raise ValidationError(f"Missing required field: '{label or key}'")
    return val


def _to_decimal(value: Any, field_name: str) -> Decimal:
    try:
        d = Decimal(str(value))
        if d <= 0:
            raise ValidationError(f"'{field_name}' must be greater than 0")
        return d
    except InvalidOperation:
        raise ValidationError(f"'{field_name}' must be a valid number, got: {value!r}")


def parse_charge_payload(data: dict) -> ChargeEventPayload:
    return ChargeEventPayload(
        order_id       = int(_require(data, "order_id",       "order_id")),
        amount         = _to_decimal(_require(data, "amount", "amount"), "amount"),
        currency       = str(_require(data, "currency",       "currency")).upper(),
        customer_email = str(_require(data, "customer_email", "customer_email")),
        provider       = str(data.get("provider", "stripe")),
        description    = str(data.get("description", "")),
        metadata       = dict(data.get("metadata") or {}),
    )


def parse_refund_payload(data: dict) -> RefundEventPayload:
    return RefundEventPayload(
        payment_id              = int(_require(data, "payment_id",              "payment_id")),
        provider_transaction_id = str(_require(data, "provider_transaction_id", "provider_transaction_id")),
        amount                  = _to_decimal(_require(data, "amount", "amount"), "amount"),
        provider                = str(data.get("provider", "stripe")),
        reason                  = str(data.get("reason", "")),
    )
