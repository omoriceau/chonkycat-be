"""
Lambda: /products and /products/{productid}
  GET     /products              -> list (see handlers/read.py for query params)
  GET     /products/{productid}  -> single product
  POST    /products               -> create
  PUT     /products/{productid}  -> partial update (also restores via {"deleted_at": null})
  DELETE  /products/{productid}  -> soft delete

Environment Variables:
  - PRODUCTS_TABLE_NAME   DynamoDB table name (aws_dynamodb_table.products.name)

This file only routes; each operation's logic lives in handlers/.
"""

import json
import traceback

from common import err, get_http_method, set_request_context
from db import get_db_client
from handlers.create import handle_create_product
from handlers.delete import handle_delete_product
from handlers.read import handle_get_product, handle_list_products
from handlers.update import handle_update_product
from shared.cors import is_preflight, preflight_response


def lambda_handler(event: dict, context) -> dict:
    print(f"[DEBUG] event: {json.dumps(event, default=str)}")

    set_request_context(event)
    if is_preflight(event):
        return preflight_response(event, methods="GET, POST, PUT, PATCH, DELETE, OPTIONS")

    try:
        db = get_db_client()
    except Exception as e:
        print(f"[ERROR] Failed to create DynamoDB client: {e}")
        print(traceback.format_exc())
        return err("Failed to initialise database client", status=500)

    path_params = event.get("pathParameters") or {}
    product_id = path_params.get("productid")
    method = get_http_method(event)
    print(f"[DEBUG] method: {method} product_id: {product_id}")

    if method == "POST":
        return handle_create_product(db, event)
    if method in ("PUT", "PATCH"):
        return handle_update_product(db, event, product_id)
    if method == "DELETE":
        return handle_delete_product(db, product_id)

    if product_id:
        params = event.get("queryStringParameters") or {}
        show_deleted = (params or {}).get("show_deleted")
        include_deleted = str(show_deleted).strip().lower() in ("1", "true", "yes")
        return handle_get_product(db, product_id, include_deleted=include_deleted)

    params = event.get("queryStringParameters") or {}
    print(f"[DEBUG] query params: {params}")
    return handle_list_products(db, params)
