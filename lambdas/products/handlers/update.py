"""
PUT/PATCH /products/{productid} — partial update (admin panel).

Also handles restoring a soft-deleted product: send {"deleted_at": null}
to clear the delete marker. Any other value for deleted_at is rejected —
deletion itself goes through the DELETE endpoint, not this one.
"""

import json
import traceback

from common import (
    err,
    is_low_stock_values,
    now_iso,
    ok,
    parse_body,
    serialize_product,
    to_decimal,
)

# Maps public-facing field names -> DynamoDB attribute names for updates.
_UPDATABLE_FIELDS = {
    "sku": "sku",
    "name": "name",
    "description": "description",
    "ingredients": "ingredients",
    "image_url": "image_url",
    "category": "category",
    "price": "price",
    "current_stock": "qty",
    "low_stock_threshold": "low_stock_threshold",
    "active": "active",
}


def handle_update_product(db, event: dict, product_id: str) -> dict:
    if not product_id:
        return err("Invalid product ID format", status=400)

    try:
        body = parse_body(event)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return err("Request body must be valid JSON", status=400)

    if not body:
        return err("Request body must include at least one field to update", status=400)

    if "deleted_at" in body and body["deleted_at"] is not None:
        return err(
            "'deleted_at' can only be cleared (set to null) here to restore a "
            "product; use DELETE /products/{productid} to delete one.",
            status=400,
        )

    try:
        existing = db.get_product(product_id)
    except Exception as e:
        print(f"[ERROR] Unexpected DynamoDB error: {e}")
        print(traceback.format_exc())
        return err(f"Database query failed: {str(e)}", status=500)

    if not existing:
        return err(f"Product with ID {product_id} not found", status=404)

    updates = {}
    null_out = set()
    try:
        for public_field, attr in _UPDATABLE_FIELDS.items():
            if public_field not in body:
                continue
            value = body[public_field]

            if value is None:
                if attr in ("sku", "name", "price", "qty", "low_stock_threshold", "active"):
                    raise ValueError(f"'{public_field}' cannot be null")
                # Nullable fields (category, description, ingredients,
                # image_url): represent "cleared" as attribute-absent, not
                # a stored NULL. Required for category since it's a GSI
                # key — DynamoDB rejects an explicit NULL there.
                null_out.add(attr)
                continue

            if attr == "price":
                value = to_decimal(value, "price")
            elif attr == "qty":
                value = int(value)
            elif attr == "low_stock_threshold":
                value = int(value)
            elif attr == "active":
                value = bool(value)
            elif attr in ("sku", "name") and isinstance(value, str):
                value = value.strip()
                if not value:
                    raise ValueError(f"'{public_field}' cannot be blank")
            elif attr == "category" and isinstance(value, str):
                value = value.strip()
                if not value:
                    null_out.add(attr)
                    continue
            updates[attr] = value
    except (ValueError, TypeError) as e:
        return err(str(e), status=400)

    if "sku" in updates and updates["sku"] != existing.get("sku"):
        try:
            clash = db.get_product_by_sku(updates["sku"])
        except Exception as e:
            print(f"[ERROR] SKU uniqueness check failed: {e}")
            print(traceback.format_exc())
            return err("Database query failed", status=500)
        if clash and clash.get("product_id") != product_id:
            return err(f"A product with sku '{updates['sku']}' already exists", status=409)

    # Recompute the sparse reorder_flag whenever qty or threshold changes.
    remove_attrs = list(null_out)
    if "qty" in updates or "low_stock_threshold" in updates:
        new_qty = updates.get("qty", existing.get("qty", 0))
        new_threshold = updates.get("low_stock_threshold", existing.get("low_stock_threshold", 0))
        if is_low_stock_values(new_qty, new_threshold):
            updates["reorder_flag"] = "true"
        else:
            remove_attrs.append("reorder_flag")

    restoring = "deleted_at" in body and body["deleted_at"] is None and existing.get("deleted_at")
    if restoring:
        remove_attrs.append("deleted_at")

    updates["updated_at"] = now_iso()

    try:
        updated = db.update_product(product_id, updates, remove_attrs)
        return ok({"data": serialize_product(updated)})
    except Exception as e:
        print(f"[ERROR] Unexpected DynamoDB error: {e}")
        print(traceback.format_exc())
        return err(f"Database write failed: {str(e)}", status=500)
