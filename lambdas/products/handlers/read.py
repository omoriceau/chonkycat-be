"""
GET /products and GET /products/{productid}

Query Parameters (list endpoint):
  - show_all       (bool, default false) — include inactive products
  - show_deleted    (bool, default false) — include soft-deleted products
                     (admin-only use case, e.g. a "trash" view)
  - page           (int,  default 1)     — page number
  - page_size      (int,  default 100, max 1000) — items per page
  - category       (str,  optional)      — filter by category
  - low_stock      (bool, default false) — only return low-stock items

NOTE ON PAGINATION: DynamoDB doesn't support SQL-style OFFSET/LIMIT paging.
To keep the existing "page"/"page_size" API contract for callers, this
handler pulls the *entire* matching result set (via Query/Scan pagination
with LastEvaluatedKey), sorts it in memory, and slices out the requested
page. That's fine for a catalog of up to a few thousand active products,
but it means every page request re-reads the whole filtered set — it does
not scale the way cursor-based (ExclusiveStartKey) pagination would. If the
catalog grows large, we should switch the frontend to cursor-based paging.
"""

import traceback

from common import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    err,
    ok,
    parse_bool,
    parse_int,
    is_deleted,
    is_low_stock,
    serialize_product,
    sort_key,
)


def handle_get_product(db, product_id: str, include_deleted: bool = False) -> dict:
    """Fetch a single product by ID. 404s on soft-deleted items unless
    include_deleted is set (e.g. ?show_deleted=true), same as the list
    endpoint's default."""
    if not product_id:
        return err("Invalid product ID format", status=400)

    try:
        item = db.get_product(product_id)

        if not item or (is_deleted(item) and not include_deleted):
            return err(f"Product with ID {product_id} not found", status=404)

        return ok({"data": serialize_product(item)})

    except Exception as e:
        print(f"[ERROR] Unexpected DynamoDB error: {e}")
        print(traceback.format_exc())
        return err(f"Database query failed: {str(e)}", status=500)


def handle_list_products(db, params: dict) -> dict:
    show_all = parse_bool(params.get("show_all"), default=False)
    show_deleted = parse_bool(params.get("show_deleted"), default=False)
    low_stock = parse_bool(params.get("low_stock"), default=False)
    page = parse_int(params.get("page"), default=1, min_val=1)
    page_size = parse_int(
        params.get("page_size"),
        default=DEFAULT_PAGE_SIZE,
        min_val=1,
        max_val=MAX_PAGE_SIZE,
    )
    category = params.get("category", "").strip() or None

    active_only = not show_all
    print(
        f"[DEBUG] parsed: show_all={show_all} show_deleted={show_deleted} "
        f"low_stock={low_stock} page={page} page_size={page_size} category={category}"
    )

    try:
        if category:
            items = db.query_by_category(category, active_only, include_deleted=show_deleted)
            if low_stock:
                items = [i for i in items if is_low_stock(i)]
            # Query on CategoryIndex already returns items sorted by name.
        elif low_stock:
            items = db.query_low_stock(active_only, include_deleted=show_deleted)
            items.sort(key=sort_key)
        else:
            items = db.scan_all(active_only, include_deleted=show_deleted)
            items.sort(key=sort_key)

    except Exception as e:
        print(f"[ERROR] Unexpected DynamoDB error: {e}")
        print(traceback.format_exc())
        return err(f"Database query failed: {str(e)}", status=500)

    total_items = len(items)
    total_pages = max(1, -(-total_items // page_size))
    start = (page - 1) * page_size
    end = start + page_size
    page_items = [serialize_product(i) for i in items[start:end]]

    return ok({
        "data": page_items,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        },
        "filters": {
            "show_all": show_all,
            "show_deleted": show_deleted,
            "low_stock": low_stock,
            "category": category,
        },
    })
