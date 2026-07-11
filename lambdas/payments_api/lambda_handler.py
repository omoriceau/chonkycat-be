"""
lambdas/payments_api/lambda_handler.py

Entry point: POST /payments

Accepts an existing order_id and creates a Stripe payment intent for it.

Environment Variables:
  - ORDERS_TABLE_NAME    DynamoDB orders table name
  - USERS_TABLE_NAME     DynamoDB users table name
  - PAYMENTS_TABLE_NAME  DynamoDB payments table name
  - EVENT_BUS_NAME       EventBridge bus name (default: chonkychonk-bus)
  - STRIPE_INTENT_FUNCTION_ARN  ARN of the Stripe Intent Lambda

NOTE: order_id is now a UUID string (DynamoDB has no auto-increment PK),
not an int like the old Postgres serial id. The request body field is
unchanged in shape — just don't assume it parses as an int anymore.

NOTE ON THE payments TABLE: the original RDS version of this lambda never
wrote a payments row at all — it only read orders/users and called out to
Stripe. This version adds a payment record (order_id / sk="PAYMENT#<intent_id>")
after the Stripe intent is created, so the payments table's ProviderTxnIndex
GSI (declared in terraform for "the future Stripe webhook handler") is
actually populated. See the matching change in stripe_webhook.

Example request body:
{
    "order_id": "9d2982e4-2a45-433b-b38e-8dba0a51a3e0"
}
"""

import json
import logging
import os
from decimal import Decimal

import boto3
from db import get_db_client

from botocore.exceptions import ClientError
from botocore.config import Config
from shared.cors import build_cors_headers, is_preflight, preflight_response

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "chonkychonk-bus")
STRIPE_INTENT_ARN     = os.environ["STRIPE_INTENT_FUNCTION_ARN"]


_events = boto3.client("events")
_lambda = boto3.client("lambda", config=Config(
    connect_timeout=5,
    read_timeout=25,
    retries={"max_attempts": 0}
))


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

# Set once at the top of lambda_handler() so ok()/err() can shape CORS
# headers for the current request without threading `event` through every
# handler function.
_current_event: dict = {}


def _cors_headers() -> dict:
    return {
        "Content-Type": "application/json",
        **build_cors_headers(_current_event, methods="POST, OPTIONS"),
    }


def ok(body: dict, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": _cors_headers(),
        "body": json.dumps(body, default=str),
    }


def err(message: str, status: int = 400) -> dict:
    return {
        "statusCode": status,
        "headers": _cors_headers(),
        "body": json.dumps({"error": message}),
    }


# ---------------------------------------------------------------------------
# Order retrieval
# ---------------------------------------------------------------------------

def get_order(db, order_id: str) -> dict:
    """Fetch the order record by order_id."""
    logger.info("get_order: order_id=%s", order_id)

    order = db.get_order(order_id)
    if not order:
        raise ValueError(f"Order {order_id} not found")

    logger.info("get_order: found, status=%s", order.get("status"))
    return order


def get_user_email(db, user_id: str) -> str:
    """Fetch user email by user_id."""
    logger.info("get_user_email: user_id=%s", user_id)

    email = db.get_user_email(user_id)
    if not email:
        raise ValueError(f"User {user_id} not found")

    logger.info("get_user_email: email=%s", email)
    return email


# ---------------------------------------------------------------------------
# EventBridge
# ---------------------------------------------------------------------------

def emit_event(detail_type: str, detail: dict):
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

def create_stripe_intent(amount: int, currency: str, order_id: str, email: str) -> dict:
    logger.info("invoking StripeIntentFunction: amount=%s currency=%s order_id=%s", amount, currency, order_id)

    response = _lambda.invoke(
        FunctionName=STRIPE_INTENT_ARN,
        InvocationType="RequestResponse",
        Payload=json.dumps({
            "amount":         amount,
            "currency":       currency,
            "order_id":       order_id,
            "customer_email": email,
        }),
    )

    logger.info("invoke returned: status=%s function_error=%s", response["StatusCode"], response.get("FunctionError"))
    payload = json.loads(response["Payload"].read())
    logger.info("invoke payload: %s", payload)

    if response.get("FunctionError"):
        logger.error("StripeIntentFunction error: %s", payload)
        raise Exception(f"Stripe Lambda failed: {payload}")

    return payload

# ---------------------------------------------------------------------------
# Core handler
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    logger.info("event=%s", json.dumps(event, default=str))

    global _current_event
    _current_event = event or {}

    if is_preflight(event):
        return preflight_response(event, methods="POST, OPTIONS")

    body = event.get("body", "")
    logger.info("raw body type=%s value=%s", type(body).__name__, repr(body))

    if body is None:
        logger.warning("body is None, defaulting to empty dict")
        body = {}
    elif isinstance(body, str):
        logger.info("body is string, attempting JSON parse")
        try:
            body = json.loads(body)
            logger.info("body parsed OK: %s", body)
        except json.JSONDecodeError as e:
            logger.error("body JSON parse failed: %s", e)
            return err("Invalid JSON body")
    elif isinstance(body, dict):
        logger.info("body is already a dict, using as-is")
    else:
        logger.error("unexpected body type: %s", type(body).__name__)
        return err("Invalid request body")

    logger.info("final body=%s", body)

    try:
        # Extract order_id from request
        order_id = body.get("order_id")
        currency = str(body.get("currency", "CAD")).lower()

        if not order_id:
            logger.warning("missing order_id")
            return err("order_id is required", status=422)

        order_id = str(order_id)

        logger.info("retrieving order: order_id=%s", order_id)
        db = get_db_client()
        order = get_order(db, order_id)
        email = get_user_email(db, order["user_id"])

        logger.info("order retrieved: status=%s total_amount=%s", order["status"], order["total_amount"])

        # Check order status
        if order["status"] != "pending":
            logger.warning("order not in pending status: status=%s", order["status"])
            return err(f"Order is in {order['status']} status, cannot create payment intent", status=409)

        total = Decimal(str(order["total_amount"]))
        logger.info("order total: %s %s", total, currency)

        logger.info("invoking StripeIntentFunction: amount=%s currency=%s order_id=%s", int(total * 100), currency, order_id)
        stripe_result = create_stripe_intent(
            amount=int(total * 100),
            currency=currency,
            order_id=order_id,
            email=email,
        )
        logger.info("stripe result: %s", stripe_result)

        # Record the payment attempt so the webhook can look it up later via
        # ProviderTxnIndex (see db.py docstring).
        try:
            db.create_payment_record(
                order_id=order_id,
                intent_id=stripe_result["intent_id"],
                status="pending",
                amount=str(total),
                currency=currency,
            )
        except Exception:
            # Don't fail the request over this — the intent already exists on
            # Stripe's side and the frontend has its client_secret. Worst
            # case, the webhook won't find a payment record to update later.
            logger.exception("Failed to write payment record for order_id=%s", order_id)

        emit_event("PaymentIntentCreated", {
            "order_id": order_id,
            "amount": str(total),
            "currency": currency,
            "stripe_payment_intent": stripe_result["intent_id"],
        })

        return ok({
            "order_id": order_id,
            "client_secret": stripe_result["client_secret"],
        })

    except ValueError as e:
        logger.error("ValueError: %s", e)
        return err(str(e), status=422)

    except Exception as e:
        logger.exception("Unhandled error: %s", e)
        return err("Internal server error", status=500)