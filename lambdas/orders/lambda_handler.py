"""
orders/lambda_handler.py

Entry point: POST /orders

Accepts a JSON body from the frontend, validates it, persists the order,
and fires an OrderCreated EventBridge event that the Payment Lambda consumes.

The frontend should include its WebSocket connection_id in the request body
so the Payment Lambda can push the result back directly.

Environment Variables:
  - ORDERS_TABLE_NAME      DynamoDB orders table name
  - PRODUCTS_TABLE_NAME    DynamoDB products table name (stock check/decrement)
  - PROMOTIONS_TABLE_NAME  DynamoDB promotions table name
  - EVENT_BUS_NAME         EventBridge bus name (default: chonkychonk-bus)

NOTE: order IDs used to be sequential integers (Postgres SERIAL). They are
now randomly generated UUID strings, since DynamoDB has no auto-increment
primary key. Any client that parsed orderId as an int needs to change to
treat it as an opaque string.

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

from models import ValidationError, parse_create_order_request, parse_update_order_request
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

    method = event.get("httpMethod", "")

    # Route based on HTTP method
    if method == "GET":
        return _handle_get_order(event)
    elif method == "POST":
        return _handle_create_order(event)
    elif method == "PUT":
        return _handle_update_order(event)
    elif method == "DELETE":
        return _handle_delete_order(event)
    else:
        return err(f"Unsupported HTTP method: {method}", status=405)


def _parse_order_id(event: dict) -> str:
    """order_id is now a UUID string, not an int — just validate it's present."""
    order_id = event["pathParameters"]["orderId"]
    if not isinstance(order_id, str) or not order_id.strip():
        raise ValueError("empty orderId")
    return order_id


def _handle_get_order(event: dict) -> dict:
    """GET /orders/{orderId}"""
    try:
        order_id = _parse_order_id(event)
    except (KeyError, TypeError, ValueError):
        return err("Invalid orderId in path", status=400)

    try:
        result = _get_service().get_order(order_id)
        if result is None:
            return err("Order not found", status=404)
        return ok({"order": result})
    except Exception:
        logger.exception("Error retrieving order")
        return err("Internal server error", status=500)


def _handle_create_order(event: dict) -> dict:
    """POST /orders"""
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


def _handle_update_order(event: dict) -> dict:
    """PUT /orders/{orderId}"""
    try:
        order_id = _parse_order_id(event)
    except (KeyError, TypeError, ValueError):
        return err("Invalid orderId in path", status=400)

    # Parse body
    body = event.get("body", "{}")
    try:
        data = json.loads(body) if isinstance(body, str) else body
    except json.JSONDecodeError:
        return err("Request body is not valid JSON", status=400)

    # Validate update fields
    try:
        update = parse_update_order_request(data)
    except ValidationError as e:
        return err(str(e), status=422)

    # Process update
    try:
        result = _get_service().update_order(order_id, update)
        if result is None:
            return err("Order not found", status=404)
        return ok({
            "message": "Order updated successfully",
            "order": result,
        })
    except ValidationError as e:
        # Business rule violations (order status, stock, etc.)
        return err(str(e), status=422)
    except ClientError as e:
        logger.exception("Infrastructure error updating order")
        return err("Internal server error", status=500)
    except Exception:
        logger.exception("Unexpected error updating order")
        return err("Internal server error", status=500)


def _handle_delete_order(event: dict) -> dict:
    """DELETE /orders/{orderId}"""
    try:
        order_id = _parse_order_id(event)
    except (KeyError, TypeError, ValueError):
        return err("Invalid orderId in path", status=400)

    try:
        success = _get_service().delete_order(order_id)
        if not success:
            return err("Order not found", status=404)
        return ok({
            "message": "Order deleted successfully",
            "order_id": order_id,
        })
    except Exception:
        logger.exception("Error deleting order")
        return err("Internal server error", status=500)
