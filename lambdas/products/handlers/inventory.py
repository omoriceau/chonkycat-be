"""
POST /inventory-check

The frontend submits a JSON array of {"sku": ..., "quantity": ...} objects,
e.g. a body of [{"sku": "SKU1", "quantity": 2}, {"sku": "SKU2", "quantity": 5}]
is a request to check whether 2 units of SKU1 and 5 units of SKU2 are in
stock.

For each SKU, checks current stock against the requested quantity. Returns
the list of SKUs that do NOT have enough stock available (including SKUs
that don't exist, or exist but are soft-deleted); an empty list means every
requested SKU is fully in stock.
"""

import traceback

from common import err, ok, parse_body


def handle_check_inventory(db, event: dict) -> dict:
    try:
        items = parse_body(event)
    except ValueError:
        return err("Invalid JSON body", status=400)

    if not isinstance(items, list) or not items:
        return err("No SKUs provided", status=400)

    requested = {}
    for item in items:
        if not isinstance(item, dict) or not item.get("sku"):
            return err("Each item must have a 'sku'", status=400)
        requested[item["sku"]] = item.get("quantity")

    try:
        products_by_sku = db.get_products_by_skus(list(requested.keys()))
    except Exception as e:
        print(f"[ERROR] Unexpected DynamoDB error: {e}")
        print(traceback.format_exc())
        return err(f"Database query failed: {str(e)}", status=500)

    insufficient = []
    for sku, raw_qty in requested.items():
        try:
            wanted = int(raw_qty)
        except (TypeError, ValueError):
            insufficient.append(sku)
            continue

        product = products_by_sku.get(sku)
        if not product or product.get("deleted_at"):
            insufficient.append(sku)
            continue

        if int(product.get("qty", 0)) < wanted:
            insufficient.append(sku)

    return ok(insufficient)
