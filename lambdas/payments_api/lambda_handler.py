"""
lambdas/payments_api/lambda_handler.py

Entry point: POST /payments

Accepts an existing order_id and creates a Stripe payment intent for it.

Environment Variables:
  - DB_HOST              PostgreSQL RDS endpoint
  - DB_PORT              PostgreSQL port (default: 5432)
  - DB_USER              Database user
  - DB_NAME              Database name
  - DB_PASSWORD_SECRET_NAME  Name of AWS Secrets Manager secret for DB password
  - EVENT_BUS_NAME       EventBridge bus name (default: chonkychonk-bus)
  - STRIPE_INTENT_FUNCTION_ARN  ARN of the Stripe Intent Lambda

Example request body:
{
    "order_id": 123
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

def ok(body: dict, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
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
# Order retrieval
# ---------------------------------------------------------------------------

def get_order_with_items(db, order_id: int) -> dict:
    """Fetch order and its items by order_id."""
    logger.info("81 get_order_with_items: order_id=%s", order_id)

    # Fetch order
    order = db.fetch_one(
        """
        SELECT id, user_id, status, total_amount, subtotal, tax_amount, shipping_amount
        FROM orders
        WHERE id = %s AND deleted_at IS NULL
        """,
        (order_id,)
    )

    if not order:
        raise ValueError(f"Order {order_id} not found")

    logger.info("96 get_order_with_items: order found, fetching items")

    # Fetch order items
    items = db.fetch_all(
        """
        SELECT product_id, quantity, unit_price, line_total, name_snapshot
        FROM order_items
        WHERE order_id = %s
        """,
        (order_id,)
    )

    logger.info("108 get_order_with_items: found %d item(s)", len(items))

    return {
        "order": order,
        "items": items,
    }


def get_user_email(db, user_id: int) -> str:
    """Fetch user email by user_id."""
    logger.info("get_user_email: user_id=%s", user_id)

    row = db.fetch_one(
        """
        SELECT email
        FROM users
        WHERE id = %s
        """,
        (user_id,)
    )

    if not row:
        raise ValueError(f"User {user_id} not found")

    email = row["email"]
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

def create_stripe_intent(amount: int, currency: str, order_id: int, email: str) -> dict:
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

        try:
            order_id = int(order_id)
        except (ValueError, TypeError):
            logger.warning("invalid order_id format: %s", order_id)
            return err("order_id must be an integer", status=422)

        logger.info("retrieving order: order_id=%s", order_id)
        db = get_db_client()
        order_data = get_order_with_items(db, order_id)
        order = order_data["order"]
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