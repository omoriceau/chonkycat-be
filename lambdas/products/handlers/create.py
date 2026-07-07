"""POST /products — create a new product (admin panel)."""

import json
import traceback

from ulid import ULID

from common import (
    DEFAULT_ACTIVE,
    DEFAULT_LOW_STOCK_THRESHOLD,
    DEFAULT_PRICE,
    DEFAULT_QTY,
    REQUIRED_CREATE_FIELDS,
    err,
    is_low_stock_values,
    now_iso,
    ok,
    parse_body,
    serialize_product,
    to_decimal,
)


def handle_create_product(db, event: dict) -> dict:
    try:
        body = parse_body(event)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return err("Request body must be valid JSON", status=400)

    missing = [f for f in REQUIRED_CREATE_FIELDS if not body.get(f)]
    if missing:
        return err(f"Missing required field(s): {', '.join(missing)}", status=400)

    sku = str(body["sku"]).strip()
    name = str(body["name"]).strip()
    if not sku:
        return err("'sku' cannot be blank", status=400)
    if not name:
        return err("'name' cannot be blank", status=400)

    try:
        price = to_decimal(body.get("price", DEFAULT_PRICE), "price")
        qty = int(body.get("current_stock", DEFAULT_QTY))
        threshold = int(body.get("low_stock_threshold", DEFAULT_LOW_STOCK_THRESHOLD))
    except (ValueError, TypeError) as e:
        return err(str(e) if isinstance(e, ValueError) else "Invalid numeric field", status=400)

    category = body.get("category")
    category = str(category).strip() if category else None

    try:
        if db.get_product_by_sku(sku):
            return err(f"A product with sku '{sku}' already exists", status=409)
    except Exception as e:
        print(f"[ERROR] SKU uniqueness check failed: {e}")
        print(traceback.format_exc())
        return err("Database query failed", status=500)

    now = now_iso()
    item = {
        "product_id": str(ULID()),
        "sku": sku,
        "name": name,
        "description": body.get("description"),
        "ingredients": body.get("ingredients"),
        "image_url": body.get("image_url"),
        "category": category,
        "price": price,
        "qty": qty,
        "low_stock_threshold": threshold,
        "active": bool(body.get("active", DEFAULT_ACTIVE)),
        "created_at": now,
        "updated_at": now,
    }
    if is_low_stock_values(qty, threshold):
        item["reorder_flag"] = "true"

    # DynamoDB rejects an explicit NULL for a GSI key attribute (category),
    # and there's no reason to store nulls for the other optional fields
    # either — omitting the attribute is the correct "empty" representation.
    item = {k: v for k, v in item.items() if v is not None}

    try:
        db.create_product(item)
        return ok({"data": serialize_product(item)}, status=201)
    except Exception as e:
        print(f"[ERROR] Unexpected DynamoDB error: {e}")
        print(traceback.format_exc())
        return err(f"Database write failed: {str(e)}", status=500)
