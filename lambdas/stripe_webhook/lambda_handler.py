"""
lambdas/stripe_webhook/lambda_handler.py

Entry point: POST /webhook

Receives Stripe webhook events and updates order status in the database.

Supported Events:
  - payment_intent.succeeded   → Update order to "completed"
  - payment_intent.payment_failed → Update order to "failed"

Environment Variables:
  - STRIPE_WEBHOOK_SECRET     Stripe webhook signing secret
  - DB_HOST                   PostgreSQL RDS endpoint
  - DB_PORT                   PostgreSQL port (default: 5432)
  - DB_USER                   Database user
  - DB_NAME                   Database name
  - DB_PASSWORD_SECRET_NAME   Name of AWS Secrets Manager secret for DB password
  - EVENT_BUS_NAME            EventBridge bus name (default: chonkychonk-bus)
"""

import json
import logging
import os
import hmac
import hashlib

import boto3
import stripe
from db import get_db_client

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
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
    if not STRIPE_WEBHOOK_SECRET:
        raise ValueError("STRIPE_WEBHOOK_SECRET not configured")
    
    if not signature:
        raise ValueError("Missing Stripe signature header")
    
    try:
        # Stripe signature format: t=<timestamp>,v1=<signature>
        timestamp, received_signature = signature.split(",")[0].split("=")[1], signature.split("v1=")[1]
        
        # Compute expected signature
        signed_content = f"{timestamp}.{body}"
        expected_signature = hmac.new(
            STRIPE_WEBHOOK_SECRET.encode(),
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
# Order status updates
# ---------------------------------------------------------------------------

def update_order_status(db, order_id: int, status: str):
    """Update order status in database."""
    logger.info("update_order_status: order_id=%s new_status=%s", order_id, status)
    
    db.execute(
        """
        UPDATE orders
        SET status = %s, updated_at = NOW()
        WHERE id = %s
        """,
        (status, order_id)
    )
    
    logger.info("update_order_status: done")


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
    
    try:
        order_id = int(order_id)
    except (ValueError, TypeError):
        logger.error("Invalid order_id in metadata: %s", order_id)
        return
    
    # Update order status
    update_order_status(db, order_id, "completed")
    
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
    
    try:
        order_id = int(order_id)
    except (ValueError, TypeError):
        logger.error("Invalid order_id in metadata: %s", order_id)
        return
    
    # Update order status
    update_order_status(db, order_id, "failed")
    
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
    
    # Connect to database
    try:
        db = get_db_client()
    except Exception as e:
        logger.error("Failed to connect to database: %s", e)
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
