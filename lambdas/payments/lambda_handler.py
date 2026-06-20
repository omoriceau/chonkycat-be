"""
payment_test/lambda_handler.py

Simplified drop-in replacement for testing the payment flow locally.
Strips out all AWS dependencies (RDS Data API, EventBridge, WebSocket, SNS)
and replaces them with:
  - In-memory "DB" (a plain dict)
  - Console logging instead of EventBridge / SNS / WebSocket

Run locally:
    pip install stripe python-dotenv
    python lambda_handler.py

Or invoke individual handlers:
    from lambda_handler import lambda_handler
    result = lambda_handler({...}, None)
"""

import json
import logging
import os
from decimal import Decimal

from dotenv import load_dotenv
import stripe
from stripe.error import StripeError

load_dotenv()  # loads STRIPE_SECRET_KEY from .env

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")

# ---------------------------------------------------------------------------
# In-memory "database" — replaces RDS Data API calls
# ---------------------------------------------------------------------------

_DB = {
    "payments": [],   # list of dicts
    "refunds":  [],
    "orders":   {},   # order_id -> {"status": ...}
}

_next_id = {"payments": 1, "refunds": 1}


def _insert_payment(order_id, provider, tx_id, amount, currency, status, paid):
    row = {
        "id":                      _next_id["payments"],
        "order_id":                order_id,
        "payment_provider":        provider,
        "provider_transaction_id": tx_id,
        "method":                  "credit_card",
        "amount":                  str(amount),
        "currency":                currency,
        "status":                  status,
        "paid_at":                 "NOW()" if paid else None,
    }
    _DB["payments"].append(row)
    _next_id["payments"] += 1
    logger.info("DB ▶ payments: %s", row)
    return row["id"]


def _insert_refund(payment_id, amount, reason, status):
    row = {
        "id":         _next_id["refunds"],
        "payment_id": payment_id,
        "amount":     str(amount),
        "reason":     reason,
        "status":     status,
    }
    _DB["refunds"].append(row)
    _next_id["refunds"] += 1
    logger.info("DB ▶ refunds: %s", row)
    return row["id"]


def _update_order_status(order_id, status):
    _DB["orders"][order_id] = {"status": status}
    logger.info("DB ▶ orders[%s].status = %s", order_id, status)


# ---------------------------------------------------------------------------
# Stripe helpers
# ---------------------------------------------------------------------------

def _to_cents(amount: Decimal) -> int:
    return int(amount * 100)


def stripe_charge(order_id, amount: Decimal, currency: str,
                  customer_email: str, description: str) -> dict:
    """
    Creates a Stripe PaymentIntent and confirms it server-side.
    Use a Stripe test card (e.g. 4242 4242 4242 4242) via the Stripe dashboard
    or supply a payment_method id directly for automated tests.
    """
    intent = stripe.PaymentIntent.create(
        amount=_to_cents(amount),
        currency=currency.lower(),
        receipt_email=customer_email,
        description=description or f"Order #{order_id}",
        metadata={"order_id": str(order_id)},
        confirm=True,
        automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
    )
    return intent


def stripe_refund(payment_intent_id: str, amount: Decimal) -> dict:
    refund = stripe.Refund.create(
        payment_intent=payment_intent_id,
        amount=_to_cents(amount),
    )
    return refund


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def ok(body: dict, status: int = 200) -> dict:
    return {"statusCode": status, "body": json.dumps(body, default=str)}


def err(message: str, status: int = 400) -> dict:
    return {"statusCode": status, "body": json.dumps({"error": message})}


# ---------------------------------------------------------------------------
# Fake downstream notifications (replaces WebSocket / EventBridge / SNS)
# ---------------------------------------------------------------------------

def notify(event_name: str, data: dict):
    """Replaces WebSocket push + EventBridge emit."""
    logger.info("NOTIFY [%s]: %s", event_name, json.dumps(data, default=str))


def alert_ops(subject: str, message: str):
    """Replaces SNS ops alert."""
    logger.warning("OPS ALERT — %s: %s", subject, message)


# ---------------------------------------------------------------------------
# Charge handler
# ---------------------------------------------------------------------------

def _handle_charge(payload: dict) -> dict:
    # Validate required fields
    for field in ("order_id", "amount", "currency", "customer_email"):
        if not payload.get(field):
            return err(f"Missing required field: '{field}'")

    order_id       = int(payload["order_id"])
    amount         = Decimal(str(payload["amount"]))
    currency       = str(payload["currency"]).upper()
    customer_email = str(payload["customer_email"])
    description    = str(payload.get("description", f"Order #{order_id}"))

    if amount <= 0:
        return err("'amount' must be greater than 0", 422)

    logger.info("Charging order=%s amount=%s %s", order_id, amount, currency)

    try:
        intent = stripe_charge(order_id, amount, currency, customer_email, description)
    except StripeError as e:
        logger.error("Stripe error: %s", e.user_message)
        _insert_payment(order_id, "Stripe", "", amount, currency, "failed", False)
        notify("payment_failed", {
            "order_id":      order_id,
            "error_code":    e.code,
            "error_message": e.user_message,
        })
        return ok({
            "success":       False,
            "error_code":    e.code,
            "error_message": e.user_message,
        }, status=402)
    except Exception as e:
        logger.exception("Provider unreachable")
        alert_ops(f"Payment provider unreachable — order #{order_id}", str(e))
        return err("Payment provider unreachable", 503)

    success = intent.status == "succeeded"
    status  = "paid" if success else "pending"

    payment_id = _insert_payment(
        order_id, "Stripe", intent.id, amount, currency, status, success
    )

    if success:
        _update_order_status(order_id, "processing")
        notify("payment_complete", {
            "order_id":   order_id,
            "payment_id": payment_id,
            "tx_id":      intent.id,
            "amount":     str(amount),
            "currency":   currency,
        })
        return ok({
            "success":    True,
            "payment_id": payment_id,
            "tx_id":      intent.id,
            "status":     status,
            "amount":     str(amount),
            "currency":   currency,
        })

    notify("payment_failed", {"order_id": order_id, "status": intent.status})
    return ok({"success": False, "status": intent.status}, status=402)


# ---------------------------------------------------------------------------
# Refund handler
# ---------------------------------------------------------------------------

def _handle_refund(payload: dict) -> dict:
    for field in ("payment_id", "provider_transaction_id", "amount"):
        if payload.get(field) is None:
            return err(f"Missing required field: '{field}'")

    payment_id = int(payload["payment_id"])
    tx_id      = str(payload["provider_transaction_id"])
    amount     = Decimal(str(payload["amount"]))
    reason     = str(payload.get("reason", ""))

    if amount <= 0:
        return err("'amount' must be greater than 0", 422)

    logger.info("Refunding payment=%s tx=%s amount=%s", payment_id, tx_id, amount)

    try:
        refund = stripe_refund(tx_id, amount)
    except StripeError as e:
        logger.error("Stripe refund error: %s", e.user_message)
        _insert_refund(payment_id, amount, reason, "failed")
        return ok({
            "success":       False,
            "error_code":    e.code,
            "error_message": e.user_message,
        }, status=402)
    except Exception as e:
        logger.exception("Refund provider error")
        return err("Refund provider unreachable", 503)

    success = refund.status == "succeeded"
    status  = "refunded" if success else "pending"

    refund_id = _insert_refund(payment_id, amount, reason, status)

    if success:
        # Find the order via payments DB and mark refunded
        payment_row = next((p for p in _DB["payments"] if p["id"] == payment_id), None)
        if payment_row:
            _update_order_status(payment_row["order_id"], "refunded")

        notify("refund_complete", {
            "refund_id":  refund_id,
            "stripe_ref": refund.id,
            "amount":     str(Decimal(refund.amount) / 100),
            "currency":   refund.currency.upper(),
        })
        return ok({
            "success":    True,
            "refund_id":  refund_id,
            "stripe_ref": refund.id,
            "status":     status,
            "amount":     str(Decimal(refund.amount) / 100),
            "currency":   refund.currency.upper(),
        })

    return ok({"success": False, "status": refund.status}, status=402)


# ---------------------------------------------------------------------------
# Main handler  (mirrors the real lambda_handler signature)
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:
    # Support direct body dict or JSON string body (API GW style)
    body = event.get("body", event)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError as e:
            return err(f"Malformed JSON: {e}")

    event_type = str(body.get("event_type", "")).strip().lower()

    if not event_type:
        return err("Missing required field: 'event_type' (charge | refund)")

    if event_type == "charge":
        return _handle_charge(body)

    if event_type == "refund":
        return _handle_refund(body)

    return err(f"Unknown event_type '{event_type}'. Expected: charge | refund")


# ---------------------------------------------------------------------------
# Quick local smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\n=== TEST: charge ===")
    charge_event = {
        "event_type":     "charge",
        "order_id":       42,
        "amount":         "19.99",
        "currency":       "CAD",
        "customer_email": "test@example.com",
        "description":    "Test order",
    }
    result = lambda_handler(charge_event, None)
    print(json.dumps(json.loads(result["body"]), indent=2))

    # To test a refund, grab the tx_id from the charge result above and pass it below.
    # Stripe test payment intents can't be refunded without a real payment_method,
    # so you'd do this after a successful charge in the Stripe test dashboard.
    #
    # print("\n=== TEST: refund ===")
    # refund_event = {
    #     "event_type":              "refund",
    #     "payment_id":              1,                        # from _DB["payments"]
    #     "provider_transaction_id": "pi_3xxx",               # from charge result
    #     "amount":                  "19.99",
    #     "reason":                  "customer request",
    # }
    # result = lambda_handler(refund_event, None)
    # print(json.dumps(json.loads(result["body"]), indent=2))