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

        # Stripe signature format: t=<timestamp>,v1=<signature>
        timestamp, received_signature = signature.split(",")[0].split("=")[1], signature.split("v1=")[1]

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

    # Emit event
    emit_event("PaymentSucceeded", {
        "order_id": order_id,
        "stripe_intent_id": intent_id,
        "amount": intent.get("amount"),
        "currency": intent.get("currency"),
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

    # Emit event
    emit_event("PaymentFailed", {
        "order_id": order_id,
        "stripe_intent_id": intent_id,
        "error": error_message,
        "amount": intent.get("amount"),
        "currency": intent.get("currency"),
    })

    logger.info("Payment failed for order_id=%s: %s", order_id, error_message)


# ---------------------------------------------------------------------------
# Main webhook handler
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    logger.info("Webhook received")

    # Get raw body and signature
    body = event.get("body", "")
    signature = event.get("headers", {}).get("stripe-signature")

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