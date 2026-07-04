"""
orders/lambda_handler.py

Entry point: POST /orders

Accepts a JSON body from the frontend, validates it, persists the order,
and fires an OrderCreated EventBridge event that the Payment Lambda consumes.

The frontend should include its WebSocket connection_id in the request body
so the Payment Lambda can push the result back directly.

Environment Variables:
  - DB_HOST              PostgreSQL RDS endpoint
  - DB_PORT              PostgreSQL port (default: 5432)
  - DB_USER              Database user
  - DB_NAME              Database name
  - DB_PASSWORD_SECRET_NAME  Name of AWS Secrets Manager secret for DB password
  - EVENT_BUS_NAME       EventBridge bus name (default: chonkychonk-bus)

Example request body:
{
    "user_id": 2,
    "customer_email": "benny.garcia@email.com",
    "connection_id": "abc123==",
    "payment_provider": "stripe",
    "promotion_code": "WELCOME10",
    "customer_notes": "Please leave at door",
    "currency": "CAD",
    "items": [
        { "product_id": 1, "quantity": 2 },
        { "product_id": 5, "quantity": 1 }
    ],
    "shipping": {
        "name": "Benny Garcia",
        "address1": "42 Maple Ave",
        "city": "Toronto",
        "province": "ON",
        "postal_code": "M5V 2H1",
        "country": "Canada"
    }
}
"""

import json
import logging
import os

from models import ValidationError, parse_create_order_request
from service import OrderService
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Module-level service — reused across warm invocations
# Lazy-initialized to avoid runtime crash if DB connection fails on cold start
_service = None

def _get_service() -> OrderService:
    global _service
    if _service is None:
        _service = OrderService()
    return _service


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def ok(body: dict, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def err(message: str, status: int = 400) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": message}),
    }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:
    logger.info("Order request received")

    # Parse body (API Gateway sends it as a string)
    body = event.get("body", "{}")
    try:
        data = json.loads(body) if isinstance(body, str) else body
    except json.JSONDecodeError:
        return err("Request body is not valid JSON", status=400)

    # Validate
    try:
        request = parse_create_order_request(data)
    except ValidationError as e:
        return err(str(e), status=422)

    # Process
    try:
        result = _get_service().create_order(request)
    except ValidationError as e:
        # Business rule violations (stock, invalid promo, etc.)
        return err(str(e), status=422)
    except ClientError as e:
        logger.exception("Infrastructure error creating order")
        return err("Internal server error", status=500)
    except Exception:
        logger.exception("Unexpected error creating order")
        return err("Internal server error", status=500)

    return ok({
        "message": "Order created. Payment is being processed.",
        "order":   result,
    }, status=201)
