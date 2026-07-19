"""
orders/lambda_handler.py

Entry points:
  GET    /orders                       admin list (see _handle_list_orders for query params)
  POST   /orders                       one-shot order placement
  GET/PUT/DELETE /orders/{orderId}     fetch/update/soft-delete a single order
  GET    /cart                         fetch the caller's open cart
  POST   /cart/items                   add (or increment) a cart line item
  PUT    /cart/items/{productId}       set a cart line item's quantity
  DELETE /cart/items/{productId}       remove a cart line item
  POST   /cart/{orderId}/checkout      turn a cart into a real pending order
  POST   /cart/claim                   transfer a guest cart onto the caller

Accepts a JSON body from the frontend, validates it, persists the order,
and fires an OrderCreated EventBridge event that the Payment Lambda consumes.

The frontend should include its WebSocket connection_id in the request body
so the Payment Lambda can push the result back directly.

The /cart* routes accept both guest and logged-in callers on the same
route (see identity.py) — every one of them except /cart/claim resolves
identity itself rather than relying on API Gateway's authorizer, which is
all-or-nothing per route.

Environment Variables:
  - ORDERS_TABLE_NAME              DynamoDB orders table name
  - PRODUCTS_TABLE_NAME            DynamoDB products table name (stock check/decrement)
  - PROMOTIONS_TABLE_NAME          DynamoDB promotions table name
  - EVENT_BUS_NAME                 EventBridge bus name (default: chonkychonk-bus)
  - CUSTOMER_COGNITO_USER_POOL_ID  Storefront Cognito pool (verifies cart Bearer tokens)
  - CUSTOMER_COGNITO_APP_CLIENT_ID Storefront Cognito app client id (optional aud check)

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

from identity import IdentityError, resolve_authenticated_user_id, resolve_user_id
from models import (
    ValidationError,
    parse_add_cart_item_request,
    parse_checkout_cart_request,
    parse_claim_cart_request,
    parse_create_order_request,
    parse_update_cart_item_request,
    parse_update_order_request,
)
from service import OrderService
from botocore.exceptions import ClientError
from shared.cors import build_cors_headers, is_preflight, preflight_response

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

# Set once at the top of lambda_handler() so ok()/err() can shape CORS
# headers for the current request without threading `event` through every
# handler function.
_current_event: dict = {}


def _cors_headers() -> dict:
    return {
        "Content-Type": "application/json",
        **build_cors_headers(_current_event),
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
# Handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:
    logger.info("Order request received")

    global _current_event
    _current_event = event or {}

    if is_preflight(event):
        return preflight_response(event)

    method = event.get("httpMethod", "")
    resource = event.get("resource", "")

    if resource.startswith("/cart"):
        return _handle_cart_request(event, resource, method)

    if resource == "/users/orders" and method == "GET":
        return _handle_list_my_orders(event)

    has_order_id = bool((event.get("pathParameters") or {}).get("orderId"))

    # Route based on HTTP method
    if method == "GET":
        return _handle_list_orders(event) if not has_order_id else _handle_get_order(event)
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


DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes")


def _parse_int(value: str | None, default: int, min_val: int, max_val: int | None = None) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    n = max(n, min_val)
    if max_val is not None:
        n = min(n, max_val)
    return n


def _handle_list_orders(event: dict) -> dict:
    """
    GET /orders

    Query Parameters:
      - page            (int,  default 1)
      - page_size       (int,  default 50, max 200)
      - status          (str,  optional)  — e.g. "pending", "completed", "failed"; "cart" also
                                             works but is otherwise excluded (see include_carts)
      - include_deleted (bool, default false) — include soft-deleted orders
      - include_carts   (bool, default false) — include open (unchecked-out) carts
    """
    params = event.get("queryStringParameters") or {}

    page = _parse_int(params.get("page"), default=1, min_val=1)
    page_size = _parse_int(params.get("page_size"), default=DEFAULT_PAGE_SIZE, min_val=1, max_val=MAX_PAGE_SIZE)
    status = (params.get("status") or "").strip() or None
    include_deleted = _parse_bool(params.get("include_deleted"))
    include_carts = _parse_bool(params.get("include_carts"))

    try:
        result = _get_service().list_orders(
            page=page,
            page_size=page_size,
            status=status,
            include_deleted=include_deleted,
            include_carts=include_carts,
        )
        return ok(result)
    except Exception:
        logger.exception("Error listing orders")
        return err("Internal server error", status=500)


def _handle_list_my_orders(event: dict) -> dict:
    """
    GET /users/orders — the caller's own order history. Self-verifying
    bearer token via identity.py (same mechanism as the /cart routes), not
    the admin authorizer that gates GET /orders above — a shopper isn't an
    admin, so this route resolves and scopes to their own sub instead.
    """
    try:
        user_id = resolve_user_id(event)
    except IdentityError as e:
        return err(str(e), status=401)

    try:
        orders = _get_service().list_my_orders(user_id)
        return ok({"orders": orders})
    except Exception:
        logger.exception("Error listing user's orders")
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


# ---------------------------------------------------------------------------
# Cart handler
#
# GET/POST/PUT/DELETE on /cart* — guest or logged-in, resolved per-request
# by identity.py rather than by an API Gateway authorizer (which is
# all-or-nothing per route and these routes need to accept both). The one
# exception is /cart/claim, which sits behind the Cognito authorizer (see
# template.yaml) since it's the one place a caller must prove who they are.
# ---------------------------------------------------------------------------

def _handle_cart_request(event: dict, resource: str, method: str) -> dict:
    try:
        if resource == "/cart" and method == "GET":
            return _handle_get_cart(event)
        if resource == "/cart/items" and method == "POST":
            return _handle_add_cart_item(event)
        if resource == "/cart/items/{productId}" and method == "PUT":
            return _handle_update_cart_item(event)
        if resource == "/cart/items/{productId}" and method == "DELETE":
            return _handle_remove_cart_item(event)
        if resource == "/cart/{orderId}/checkout" and method == "POST":
            return _handle_checkout_cart(event)
        if resource == "/cart/claim" and method == "POST":
            return _handle_claim_cart(event)
        return err(f"Unsupported route: {method} {resource}", status=405)
    except IdentityError as e:
        return err(str(e), status=401)


def _handle_get_cart(event: dict) -> dict:
    """GET /cart"""
    user_id = resolve_user_id(event)
    try:
        return ok({"cart": _get_service().get_cart(user_id)})
    except Exception:
        logger.exception("Error retrieving cart")
        return err("Internal server error", status=500)


def _handle_add_cart_item(event: dict) -> dict:
    """POST /cart/items"""
    user_id = resolve_user_id(event)

    body = event.get("body", "{}")
    try:
        data = json.loads(body) if isinstance(body, str) else body
    except json.JSONDecodeError:
        return err("Request body is not valid JSON", status=400)

    try:
        request = parse_add_cart_item_request(data)
    except ValidationError as e:
        return err(str(e), status=422)

    try:
        cart = _get_service().add_cart_item(user_id, request.product_id, request.quantity)
        return ok({"cart": cart}, status=201)
    except ValidationError as e:
        return err(str(e), status=422)
    except Exception:
        logger.exception("Error adding cart item")
        return err("Internal server error", status=500)


def _parse_cart_product_id(event: dict) -> str:
    product_id = event["pathParameters"]["productId"]
    if not isinstance(product_id, str) or not product_id.strip():
        raise ValueError("empty productId")
    return product_id


def _handle_update_cart_item(event: dict) -> dict:
    """PUT /cart/items/{productId}"""
    user_id = resolve_user_id(event)

    try:
        product_id = _parse_cart_product_id(event)
    except (KeyError, TypeError, ValueError):
        return err("Invalid productId in path", status=400)

    body = event.get("body", "{}")
    try:
        data = json.loads(body) if isinstance(body, str) else body
    except json.JSONDecodeError:
        return err("Request body is not valid JSON", status=400)

    try:
        request = parse_update_cart_item_request(data)
    except ValidationError as e:
        return err(str(e), status=422)

    try:
        cart = _get_service().update_cart_item(user_id, product_id, request.quantity)
        return ok({"cart": cart})
    except ValidationError as e:
        return err(str(e), status=422)
    except Exception:
        logger.exception("Error updating cart item")
        return err("Internal server error", status=500)


def _handle_remove_cart_item(event: dict) -> dict:
    """DELETE /cart/items/{productId}"""
    user_id = resolve_user_id(event)

    try:
        product_id = _parse_cart_product_id(event)
    except (KeyError, TypeError, ValueError):
        return err("Invalid productId in path", status=400)

    try:
        cart = _get_service().remove_cart_item(user_id, product_id)
        return ok({"cart": cart})
    except ValidationError as e:
        return err(str(e), status=422)
    except Exception:
        logger.exception("Error removing cart item")
        return err("Internal server error", status=500)


def _handle_checkout_cart(event: dict) -> dict:
    """POST /cart/{orderId}/checkout"""
    user_id = resolve_user_id(event)

    try:
        order_id = event["pathParameters"]["orderId"]
        if not isinstance(order_id, str) or not order_id.strip():
            raise ValueError("empty orderId")
    except (KeyError, TypeError, ValueError):
        return err("Invalid orderId in path", status=400)

    body = event.get("body", "{}")
    try:
        data = json.loads(body) if isinstance(body, str) else body
    except json.JSONDecodeError:
        return err("Request body is not valid JSON", status=400)

    try:
        request = parse_checkout_cart_request(data)
    except ValidationError as e:
        return err(str(e), status=422)

    try:
        result = _get_service().checkout_cart(user_id, order_id, request)
    except ValidationError as e:
        return err(str(e), status=422)
    except ClientError:
        logger.exception("Infrastructure error checking out cart")
        return err("Internal server error", status=500)
    except Exception:
        logger.exception("Unexpected error checking out cart")
        return err("Internal server error", status=500)

    return ok({
        "message": "Order created. Payment is being processed.",
        "order":   result,
    }, status=201)


def _handle_claim_cart(event: dict) -> dict:
    """
    POST /cart/claim — behind the Cognito authorizer (template.yaml), so the
    caller's identity comes from API Gateway's already-verified claims, not
    from the request body. Merges/re-keys the guest cart named in the body
    onto that identity.
    """
    authenticated_user_id = resolve_authenticated_user_id(event)

    body = event.get("body", "{}")
    try:
        data = json.loads(body) if isinstance(body, str) else body
    except json.JSONDecodeError:
        return err("Request body is not valid JSON", status=400)

    try:
        request = parse_claim_cart_request(data)
    except ValidationError as e:
        return err(str(e), status=422)

    try:
        cart = _get_service().claim_guest_cart(authenticated_user_id, request.guest_id)
        return ok({"cart": cart})
    except Exception:
        logger.exception("Error claiming guest cart")
        return err("Internal server error", status=500)
