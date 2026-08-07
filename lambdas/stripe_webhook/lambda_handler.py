"""
lambdas/stripe_webhook/lambda_handler.py

Entry point: POST /webhook

Receives Stripe webhook events and updates order status in the database.

Supported Events:
  - payment_intent.succeeded   -> Update order to "completed"
  - payment_intent.payment_failed -> Update order to "failed"

Environment Variables:
  - STRIPE_WEBHOOK_SECRET     Stripe webhook signing secret
  - ORDERS_TABLE_NAME         DynamoDB orders table name
  - PAYMENTS_TABLE_NAME       DynamoDB payments table name
  - EVENT_BUS_NAME            EventBridge bus name (default: chonkychonk-bus)

NOTE: order_id is now a UUID string (metadata.order_id on the Stripe intent
is already a string, so no behavior change there — the old int(order_id)
coercion is just removed since it's no longer needed or correct).

NOTE ON THE payments TABLE: the original RDS version of this lambda only
ever updated `orders.status` — it never touched a payments row. This
version also looks up and updates the matching payment record via
ProviderTxnIndex, so the payments table (and its GSI) actually reflects
what happened. See payments_api/db.py's docstring for the other half of
this.
"""

import json
import logging
import os
import hmac
import hashlib
from decimal import Decimal

import boto3
import stripe
from db import get_db_client
from secret_store import get_secret

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# STRIPE_WEBHOOK_SECRET_NAME is a Secrets Manager secret *name*, not the
# actual signing secret — the raw value is fetched (and cached) via
# get_secret() below. template.yaml grants this function
# secretsmanager:GetSecretValue for exactly this secret's ARN.
STRIPE_WEBHOOK_SECRET_NAME = os.environ.get("STRIPE_WEBHOOK_SECRET_NAME")
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "chonkychonk-bus")

_events = boto3.client("events")


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def ok(body: dict = None, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body or {}, default=str),
    }


def err(message: str, status: int = 400) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({"error": message}),
    }


# ---------------------------------------------------------------------------
# Stripe signature verification
# ---------------------------------------------------------------------------

def _get_header(event: dict, name: str) -> str | None:
    """
    Case-insensitive header lookup — API Gateway's REST API proxy
    integration preserves the caller's original header casing (unlike HTTP
    APIs, which lowercase everything), and Stripe sends "Stripe-Signature"
    capitalized. A plain event["headers"].get("stripe-signature") lookup
    silently misses it on every request — same problem identity.py's
    _get_header() already solves for the orders Lambda's Authorization/
    X-Guest-Id headers.
    """
    headers = (event or {}).get("headers") or {}
    lname = name.lower()
    for key, value in headers.items():
        if key.lower() == lname:
            return value
    return None


def verify_stripe_signature(body: str, signature: str) -> dict:
    """
    Verify the Stripe webhook signature.

    Args:
        body: Raw request body as string
        signature: Stripe signature from headers

    Returns:
        Parsed event dict if valid

    Raises:
        ValueError: If signature is invalid
    """
    if not STRIPE_WEBHOOK_SECRET_NAME:
        raise ValueError("STRIPE_WEBHOOK_SECRET_NAME not configured")

    if not signature:
        raise ValueError("Missing Stripe signature header")

    try:
        webhook_secret = get_secret(STRIPE_WEBHOOK_SECRET_NAME)

        # Stripe signature format: "t=<timestamp>,v1=<signature>" — but
        # Stripe sometimes also includes a legacy "v0=<signature>" field in
        # the same header (seen in practice on API version 2026-05-27).
        # Splitting on the literal substring "v1=" instead of parsing each
        # comma-separated key=value pair grabs everything after it,
        # including a trailing ",v0=..." — silently corrupting
        # received_signature so it can never match, even with the right
        # secret. Parse it as actual key=value pairs instead.
        fields = dict(part.split("=", 1) for part in signature.split(","))
        timestamp = fields.get("t")
        received_signature = fields.get("v1")
        if not timestamp or not received_signature:
            raise ValueError("Malformed Stripe-Signature header")

        # Compute expected signature
        signed_content = f"{timestamp}.{body}"
        expected_signature = hmac.new(
            webhook_secret.encode(),
            signed_content.encode(),
            hashlib.sha256
        ).hexdigest()

        # Compare signatures
        if not hmac.compare_digest(expected_signature, received_signature):
            raise ValueError("Invalid Stripe signature")

        logger.info("Stripe signature verified")
        return json.loads(body)

    except (ValueError, IndexError, json.JSONDecodeError) as e:
        logger.error("Signature verification failed: %s", e)
        raise ValueError(f"Invalid webhook: {str(e)}")


# ---------------------------------------------------------------------------
# Order + payment status updates
# ---------------------------------------------------------------------------

def _cents_to_amount(cents: int) -> Decimal:
    """Stripe amounts are always in the smallest currency unit (cents for
    CAD/USD) — the email templates expect a decimal dollar amount, same as
    what orders/service.py sends elsewhere. Takes a plain int, not
    Optional[int] — callers resolve a missing amount to 0 themselves (e.g.
    intent.get("amount", 0)) rather than this function accepting and
    internally guarding against None."""
    dollars: Decimal = Decimal(cents) / Decimal(100)
    return dollars.quantize(Decimal("0.01"))


def update_order_status(db, order_id: str, status: str):
    """Update order status in DynamoDB."""
    logger.info("update_order_status: order_id=%s new_status=%s", order_id, status)
    db.update_order_status(order_id, status)
    logger.info("update_order_status: done")


def update_payment_status(db, intent_id: str, status: str, error_message: str | None = None):
    """
    Look up the payment record this intent belongs to (via ProviderTxnIndex)
    and update its status. Logs a warning rather than failing if no matching
    record exists — the order status update is the important part and
    already happened.
    """
    payment = db.find_payment_by_intent(intent_id)
    if not payment:
        logger.warning("No payment record found for intent_id=%s — skipping payment status update", intent_id)
        return
    db.update_payment_status(payment["order_id"], payment["sk"], status, error_message)
    logger.info("update_payment_status: order_id=%s sk=%s status=%s", payment["order_id"], payment["sk"], status)


def emit_event(detail_type: str, detail: dict):
    """Emit event to EventBridge."""
    try:
        logger.info("emit_event: detail_type=%s detail=%s", detail_type, detail)
        _events.put_events(Entries=[{
            "Source": "chonkychonk.payments",
            "DetailType": detail_type,
            "Detail": json.dumps(detail, default=str),
            "EventBusName": EVENT_BUS_NAME,
        }])
        logger.info("emit_event: success")
    except ClientError as e:
        logger.exception("EventBridge failure: %s", e)


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

def _order_email_fields(db, order_id: str) -> dict:
    """
    Full order + item detail for the confirmation/failure email, in the
    exact shape email_service/lambda_handler.py's handlers expect (this
    used to be built by orders/service.py's _emit_order_created at order-
    creation time — moved here so the email only goes out once payment
    actually resolves, not when the order is merely placed).

    Returns {} if the order can't be found — the caller still emits the
    event with the fields it already has (order_id, amount, currency), so
    the email handler's own "Missing required fields" check still applies
    rather than this raising and dropping the event entirely.
    """
    result = db.get_order_with_children(order_id)
    if result is None:
        logger.warning("Order %s not found — emitting event without email detail", order_id)
        return {}

    order = result["order"]
    items = result["items"]

    shipping_address = ", ".join(filter(None, [
        order.get("shipping_address1"),
        order.get("shipping_address2"),
        order.get("shipping_city"),
        order.get("shipping_province"),
        order.get("shipping_postal_code"),
        order.get("shipping_country"),
    ]))

    applied_promotions = order.get("applied_promotions") or []
    promotion = applied_promotions[0] if applied_promotions else None

    return {
        "customer_email":   order.get("customer_email"),
        "subtotal":         str(order.get("subtotal", "0")),
        "discount":         str(promotion["discount_amount"]) if promotion else "0",
        "tax":              str(order.get("tax_amount", "0")),
        "shipping_fee":     str(order.get("shipping_amount", "0")),
        "promotion_code":   promotion["code"] if promotion else None,
        "shipping_name":    order.get("shipping_name"),
        "shipping_address": shipping_address,
        "customer_notes":   order.get("customer_notes"),
        "items": [
            {
                "name":       item.get("name_snapshot"),
                "quantity":   int(item.get("quantity", 0)),
                "unit_price": str(item.get("unit_price", "0")),
                "line_total": str(item.get("line_total", "0")),
            }
            for item in items
        ],
    }


def handle_payment_intent_succeeded(db, event: dict):
    """Handle payment_intent.succeeded event."""
    logger.info("handle_payment_intent_succeeded")

    intent = event.get("data", {}).get("object", {})
    intent_id = intent.get("id")
    metadata = intent.get("metadata", {})
    order_id = metadata.get("order_id")

    logger.info("intent_id=%s order_id=%s", intent_id, order_id)

    if not order_id:
        logger.warning("No order_id in metadata, skipping")
        return

    # Update order status
    update_order_status(db, order_id, "completed")

    # Update payment record, if we have one
    update_payment_status(db, intent_id, "succeeded")

    # Emit event — the confirmation email fires off this, now that payment
    # has actually succeeded, rather than at order-creation time.
    emit_event("PaymentSucceeded", {
        "order_id": order_id,
        "stripe_intent_id": intent_id,
        "amount": str(_cents_to_amount(intent.get("amount", 0))),
        "currency": (intent.get("currency") or "cad").upper(),
        **_order_email_fields(db, order_id),
    })

    logger.info("Payment succeeded for order_id=%s", order_id)


def handle_payment_intent_payment_failed(db, event: dict):
    """Handle payment_intent.payment_failed event."""
    logger.info("handle_payment_intent_payment_failed")

    intent = event.get("data", {}).get("object", {})
    intent_id = intent.get("id")
    metadata = intent.get("metadata", {})
    order_id = metadata.get("order_id")
    last_error = intent.get("last_payment_error", {})
    error_message = last_error.get("message", "Unknown error")

    logger.info("intent_id=%s order_id=%s error=%s", intent_id, order_id, error_message)

    if not order_id:
        logger.warning("No order_id in metadata, skipping")
        return

    # Update order status
    update_order_status(db, order_id, "failed")

    # Update payment record, if we have one
    update_payment_status(db, intent_id, "failed", error_message=error_message)

    order_fields = _order_email_fields(db, order_id)

    # Emit event
    emit_event("PaymentFailed", {
        "order_id": order_id,
        "stripe_intent_id": intent_id,
        "reason": error_message,
        "amount": str(_cents_to_amount(intent.get("amount", 0))),
        "currency": (intent.get("currency") or "cad").upper(),
        "customer_email": order_fields.get("customer_email"),
    })

    logger.info("Payment failed for order_id=%s: %s", order_id, error_message)


# ---------------------------------------------------------------------------
# Main webhook handler
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    logger.info("Webhook received")

    # Get raw body and signature
    body = event.get("body", "")
    signature = _get_header(event, "stripe-signature")

    logger.info("body length=%d signature=%s", len(body) if body else 0, signature[:20] if signature else None)

    # Verify signature
    try:
        webhook_event = verify_stripe_signature(body, signature)
    except ValueError as e:
        logger.error("Signature verification failed: %s", e)
        return err(str(e), status=401)

    event_type = webhook_event.get("type")
    logger.info("event_type=%s", event_type)

    # Connect to DynamoDB
    try:
        db = get_db_client()
    except Exception as e:
        logger.error("Failed to create DynamoDB client: %s", e)
        return err("Database connection failed", status=500)

    # Handle specific event types
    try:
        if event_type == "payment_intent.succeeded":
            handle_payment_intent_succeeded(db, webhook_event)

        elif event_type == "payment_intent.payment_failed":
            handle_payment_intent_payment_failed(db, webhook_event)

        else:
            logger.info("Unhandled event type: %s", event_type)

    except Exception as e:
        logger.exception("Error handling webhook event: %s", e)
        return err("Failed to process webhook", status=500)

    # Always return 200 to acknowledge receipt
    logger.info("Webhook processed successfully")
    return ok({"status": "received"}, status=200)