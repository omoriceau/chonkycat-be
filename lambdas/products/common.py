"""
Shared helpers for the products Lambda's CRUD handlers.

Kept in one place so create/read/update/delete don't each reimplement
response shaping, validation, or the low-stock/soft-delete rules —
those are business logic, not per-operation formatting, and drifting
copies of them across files is exactly the bug risk splitting-by-file
would otherwise introduce.
"""

import base64
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl

from shared.cors import build_cors_headers

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 1000

# Matches the NOT NULL columns with no default in the original Postgres
# schema. Everything else (price, qty, low_stock_threshold, active,
# category, description, ingredients, image_url) is optional / defaulted.
REQUIRED_CREATE_FIELDS = ("sku", "name")

DEFAULT_PRICE = Decimal("0.00")
DEFAULT_QTY = 0
DEFAULT_LOW_STOCK_THRESHOLD = 10  # matches SQL DEFAULT 10
DEFAULT_ACTIVE = True


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

# Set once at the top of lambda_handler() so ok()/err()/no_content() (called
# from deep inside handlers/ without the event in scope) can still shape
# CORS headers for the current request.
_current_event: dict = {}


def set_request_context(event: dict) -> None:
    global _current_event
    _current_event = event or {}


def cors_headers() -> dict:
    return {
        "Content-Type": "application/json",
        **build_cors_headers(_current_event, methods="GET, POST, PUT, PATCH, DELETE, OPTIONS"),
    }


def _json_default(value):
    """json.dumps default= handler — DynamoDB numbers come back as Decimal."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return str(value)


def ok(body: dict, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": cors_headers(),
        "body": json.dumps(body, default=_json_default),
    }


def no_content() -> dict:
    return {"statusCode": 204, "headers": cors_headers(), "body": ""}


def err(message: str, status: int = 400) -> dict:
    return {
        "statusCode": status,
        "headers": cors_headers(),
        "body": json.dumps({"error": message}),
    }


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes")


def parse_int(value: str | None, default: int, min_val: int = 1, max_val: int | None = None) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if min_val is not None:
        n = max(n, min_val)
    if max_val is not None:
        n = min(n, max_val)
    return n


def parse_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    return json.loads(raw)


def parse_form_body(event: dict) -> dict:
    """Parse an application/x-www-form-urlencoded body into a flat dict.
    Used by the inventory-check endpoint, whose frontend submits an actual
    HTML form rather than JSON: each field name is a SKU, and its value is
    the quantity being requested for that SKU."""
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    return dict(parse_qsl(raw, keep_blank_values=True))


def get_http_method(event: dict) -> str:
    # REST API (v1) puts it at the top level; HTTP API (v2) nests it.
    if "httpMethod" in event:
        return event["httpMethod"]
    return event.get("requestContext", {}).get("http", {}).get("method", "GET")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_decimal(value, field: str) -> Decimal:
    """JSON numbers arrive as float/int — DynamoDB requires Decimal."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"'{field}' must be a valid number")


# ---------------------------------------------------------------------------
# Product-shape / business-rule helpers
# ---------------------------------------------------------------------------

def is_low_stock_values(qty, threshold) -> bool:
    return qty <= threshold


def is_deleted(item: dict) -> bool:
    """Soft-delete check — mirrors the original `deleted_at IS NOT NULL`."""
    return bool(item.get("deleted_at"))


def is_low_stock(item: dict) -> bool:
    return is_low_stock_values(item.get("qty", 0), item.get("low_stock_threshold", 0))


def serialize_product(item: dict) -> dict:
    """Shape a raw DynamoDB item into the same response shape the API used
    to return from Postgres (keeps the frontend contract stable)."""
    return {
        "id": item.get("product_id"),
        "sku": item.get("sku"),
        "name": item.get("name"),
        "description": item.get("description"),
        "ingredients": item.get("ingredients"),
        "image_url": item.get("image_url"),
        "category": item.get("category"),
        "price": item.get("price"),
        "current_stock": item.get("qty"),
        "low_stock_threshold": item.get("low_stock_threshold"),
        "active": item.get("active"),
        "is_low_stock": is_low_stock(item),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "deleted_at": item.get("deleted_at"),
    }


def sort_key(item: dict):
    return (item.get("category") or "", item.get("name") or "")
