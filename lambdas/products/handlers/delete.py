"""
DELETE /products/{productid} — soft delete (admin panel).

Mirrors the original Postgres pattern of setting `deleted_at` rather than
removing the row: historical orders that reference this product_id keep
working, and the product can be restored later via
PUT /products/{productid} {"deleted_at": null}.
"""

import traceback

from common import err, is_deleted, no_content, now_iso


def handle_delete_product(db, product_id: str) -> dict:
    if not product_id:
        return err("Invalid product ID format", status=400)

    try:
        existing = db.get_product(product_id)
        if not existing or is_deleted(existing):
            return err(f"Product with ID {product_id} not found", status=404)

        db.soft_delete_product(product_id, now_iso())
        return no_content()
    except Exception as e:
        print(f"[ERROR] Unexpected DynamoDB error: {e}")
        print(traceback.format_exc())
        return err(f"Database write failed: {str(e)}", status=500)
