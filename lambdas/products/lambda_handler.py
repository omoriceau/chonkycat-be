"""
Lambda: GET /products
Query Parameters:
  - show_all    (bool, default false) — include inactive products
  - page        (int,  default 1)     — page number
  - page_size   (int,  default 100, max 1000) — items per page
  - category    (str,  optional)      — filter by category
  - low_stock   (bool, default false) — only return low-stock items

Environment Variables:
  - PRODUCTS_TABLE_NAME   DynamoDB table name (aws_dynamodb_table.products.name)

Data source: DynamoDB (products table).
  - Single product lookup -> GetItem on product_id.
  - category filter        -> Query on CategoryIndex (hash=category, range=name),
                               already sorted by name.
  - low_stock filter only  -> Query on the sparse ReorderIndex
                               (hash=reorder_flag="true", range=product_id).
  - no filters              -> full table Scan (no index spans every category).

NOTE ON PAGINATION: DynamoDB doesn't support SQL-style OFFSET/LIMIT paging.
To keep the existing "page"/"page_size" API contract for callers, this
handler pulls the *entire* matching result set (via Query/Scan pagination
with LastEvaluatedKey), sorts it in memory, and slices out the requested
page. That's fine for a catalog of up to a few thousand active products,
but it means every page request re-reads the whole filtered set — it does
not scale the way cursor-based (ExclusiveStartKey) pagination would. If the
catalog grows large, we should switch the frontend to cursor-based paging.
"""

import json
import os
import traceback
from decimal import Decimal

from db import get_db_client

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE     = 1000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def cors_headers() -> dict:
    """Return CORS headers to allow cross-origin requests."""
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
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


def err(message: str, status: int = 400) -> dict:
    return {
        "statusCode": status,
        "headers": cors_headers(),
        "body": json.dumps({"error": message}),
    }


def _is_low_stock(item: dict) -> bool:
    qty = item.get("qty", 0)
    threshold = item.get("low_stock_threshold", 0)
    return qty <= threshold


def _serialize_product(item: dict) -> dict:
    """Shape a raw DynamoDB item into the same response shape the API used to
    return from Postgres (keeps the frontend contract stable)."""
    return {
        "id": item.get("product_id"),
        "sku": item.get("sku"),
        "name": item.get("name"),
        "description": item.get("description"),
        "image_url": item.get("image_url"),
        "category": item.get("category"),
        "price": item.get("price"),
        "current_stock": item.get("qty"),
        "low_stock_threshold": item.get("low_stock_threshold"),
        "active": item.get("active"),
        "is_low_stock": _is_low_stock(item),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


def _sort_key(item: dict):
    return (item.get("category") or "", item.get("name") or "")


# ---------------------------------------------------------------------------
# Single Product Handler
# ---------------------------------------------------------------------------

def _handle_get_product(db, product_id: str) -> dict:
    """Fetch a single product by ID."""
    if not product_id:
        return err("Invalid product ID format", status=400)

    try:
        item = db.get_product(product_id)

        if not item:
            return err(f"Product with ID {product_id} not found", status=404)

        return ok({"data": _serialize_product(item)})

    except Exception as e:
        print(f"[ERROR] Unexpected DynamoDB error: {e}")
        print(traceback.format_exc())
        return err(f"Database query failed: {str(e)}", status=500)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:
    print(f"[DEBUG] event: {json.dumps(event, default=str)}")
    print(f"[DEBUG] env: PRODUCTS_TABLE_NAME={os.environ.get('PRODUCTS_TABLE_NAME')}")

    # -- DB client -----------------------------------------------------------
    try:
        db = get_db_client()
        print(f"[DEBUG] db client created: {type(db)}")
    except Exception as e:
        print(f"[ERROR] Failed to create DynamoDB client: {e}")
        print(traceback.format_exc())
        return err("Failed to initialise database client", status=500)

    # -- Check for product ID in path parameters ----------------------------
    path_params = event.get("pathParameters") or {}
    product_id = path_params.get("productid")

    if product_id:
        return _handle_get_product(db, product_id)

    params = event.get("queryStringParameters") or {}
    print(f"[DEBUG] query params: {params}")

    # -- Parse query params --------------------------------------------------
    show_all  = parse_bool(params.get("show_all"),  default=False)
    low_stock = parse_bool(params.get("low_stock"), default=False)
    page      = parse_int(params.get("page"),      default=1, min_val=1)
    page_size = parse_int(
        params.get("page_size"),
        default=DEFAULT_PAGE_SIZE,
        min_val=1,
        max_val=MAX_PAGE_SIZE,
    )
    category  = params.get("category", "").strip() or None

    active_only = not show_all
    print(f"[DEBUG] parsed: show_all={show_all} low_stock={low_stock} page={page} page_size={page_size} category={category}")

    # -- Fetch matching items --------------------------------------------
    try:
        if category:
            items = db.query_by_category(category, active_only)
            if low_stock:
                items = [i for i in items if _is_low_stock(i)]
            # Query on CategoryIndex already returns items sorted by name.
        elif low_stock:
            items = db.query_low_stock(active_only)
            items.sort(key=_sort_key)
        else:
            items = db.scan_all(active_only)
            items.sort(key=_sort_key)

    except Exception as e:
        print(f"[ERROR] Unexpected DynamoDB error: {e}")
        print(traceback.format_exc())
        return err(f"Database query failed: {str(e)}", status=500)

    # -- Paginate in memory (see module docstring for why) --------------
    total_items = len(items)
    total_pages = max(1, -(-total_items // page_size))
    start = (page - 1) * page_size
    end = start + page_size
    page_items = [_serialize_product(i) for i in items[start:end]]

    return ok({
        "data": page_items,
        "pagination": {
            "page":        page,
            "page_size":   page_size,
            "total_items": total_items,
            "total_pages": total_pages,
            "has_next":    page < total_pages,
            "has_prev":    page > 1,
        },
        "filters": {
            "show_all":  show_all,
            "low_stock": low_stock,
            "category":  category,
        },
    })