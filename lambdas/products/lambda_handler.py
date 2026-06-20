"""
Lambda: GET /products
Query Parameters:
  - show_all    (bool, default false) — include inactive products
  - page        (int,  default 1)     — page number
  - page_size   (int,  default 100, max 1000) — items per page
  - category    (str,  optional)      — filter by category
  - low_stock   (bool, default false) — only return low-stock items

Environment Variables:
  - DB_HOST      PostgreSQL host
  - DB_PORT      PostgreSQL port (default 5432)
  - DB_USER      Database user
  - DB_PASSWORD  Database password
  - DB_NAME      Database name
"""

import json
import os
import traceback

# Import DB helper (uses PostgreSQL)
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


def ok(body: dict, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": cors_headers(),
        "body": json.dumps(body, default=str),
    }


def err(message: str, status: int = 400) -> dict:
    return {
        "statusCode": status,
        "headers": cors_headers(),
        "body": json.dumps({"error": message}),
    }


def rows_to_dicts(column_metadata: list, records: list) -> list[dict]:
    """Convert RDS Data API response records into a list of dicts."""
    columns = [col["name"] for col in column_metadata]
    result = []
    for record in records:
        row = {}
        for col, field in zip(columns, record):
            value = next(iter(field.values())) if field != {"isNull": True} else None
            row[col] = value
        result.append(row)
    return result


# ---------------------------------------------------------------------------
# Single Product Handler
# ---------------------------------------------------------------------------

def _handle_get_product(db, product_id: str) -> dict:
    """Fetch a single product by ID."""
    try:
        product_id_int = int(product_id)
    except (ValueError, TypeError):
        return err("Invalid product ID format", status=400)

    sql = """
        SELECT
            p.id,
            p.sku,
            p.name,
            p.description,
            p.image_url,
            p.category,
            p.price,
            p.qty             AS current_stock,
            p.low_stock_threshold,
            p.active,
            CASE WHEN p.qty <= p.low_stock_threshold THEN 1 ELSE 0 END AS is_low_stock,
            p.created_at,
            p.updated_at
        FROM products p
        WHERE p.id = $1
    """
    
    try:
        resp = db.execute_statement(
            sql=sql,
            parameters=[{"name": "id", "value": {"longValue": product_id_int}}],
            includeResultMetadata=True,
        )
        
        if not resp.get("records"):
            return err(f"Product with ID {product_id} not found", status=404)
        
        product = rows_to_dicts(resp["columnMetadata"], resp["records"])[0]
        
        return ok({
            "data": product
        })
        
    except ConnectionError as e:
        print(f"[ERROR] Database connection error: {e}")
        print(traceback.format_exc())
        return err(f"Database connection failed: {str(e)}", status=503)
    except Exception as e:
        print(f"[ERROR] Unexpected database error: {e}")
        print(traceback.format_exc())
        return err(f"Database query failed: {str(e)}", status=500)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:
    print(f"[DEBUG] event: {json.dumps(event, default=str)}")
    print(f"[DEBUG] env: DB_HOST={os.environ.get('DB_HOST')} DB_PORT={os.environ.get('DB_PORT')} DB_NAME={os.environ.get('DB_NAME')} DB_USER={os.environ.get('DB_USER')} DB_PASSWORD={'***' if os.environ.get('DB_PASSWORD') else 'NOT SET'}")

    # -- DB client -----------------------------------------------------------
    try:
        db = get_db_client()
        print(f"[DEBUG] db client created: {type(db)}")
    except Exception as e:
        print(f"[ERROR] Failed to create DB client: {e}")
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

    offset = (page - 1) * page_size
    print(f"[DEBUG] parsed: show_all={show_all} low_stock={low_stock} page={page} page_size={page_size} category={category} offset={offset}")

    # -- Build WHERE clauses & parameter list --------------------------------
    conditions   = []
    sql_params   = []
    param_index  = 1

    if not show_all:
        conditions.append(f"p.active = ${param_index}")
        sql_params.append({"name": "active", "value": {"booleanValue": True}})
        param_index += 1

    if category:
        conditions.append(f"p.category = ${param_index}")
        sql_params.append({"name": "category", "value": {"stringValue": category}})
        param_index += 1

    if low_stock:
        conditions.append("p.qty <= p.low_stock_threshold")

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    # -- Count query ---------------------------------------------------------
    count_sql = f"SELECT COUNT(*) AS total FROM products p {where_clause}"
    print(f"[DEBUG] count_sql: {count_sql}")
    print(f"[DEBUG] sql_params: {sql_params}")

    # -- Data query ----------------------------------------------------------
    data_sql = f"""
        SELECT
            p.id,
            p.sku,
            p.name,
            p.description,
            p.image_url,
            p.category,
            p.price,
            p.qty             AS current_stock,
            p.low_stock_threshold,
            p.active,
            CASE WHEN p.qty <= p.low_stock_threshold THEN 1 ELSE 0 END AS is_low_stock,
            p.created_at,
            p.updated_at
        FROM products p
        {where_clause}
        ORDER BY p.category ASC, p.name ASC
        LIMIT ${param_index}
        OFFSET ${param_index + 1}
    """
    print(f"[DEBUG] data_sql: {data_sql}")

    paginated_params = sql_params + [
        {"name": "limit",  "value": {"longValue": page_size}},
        {"name": "offset", "value": {"longValue": offset}},
    ]

    # -- Execute -------------------------------------------------------------
    try:
        print("[DEBUG] executing count query...")
        count_resp = db.execute_statement(
            sql=count_sql,
            parameters=sql_params,
        )
        print(f"[DEBUG] count_resp: {count_resp}")
        
        if not count_resp.get("records"):
            print("[ERROR] Count query returned no records")
            return err("Failed to retrieve product count from database", status=500)
        
        total_items = count_resp["records"][0][0]["longValue"]

        print("[DEBUG] executing data query...")
        data_resp = db.execute_statement(
            sql=data_sql,
            parameters=paginated_params,
            includeResultMetadata=True,
        )
        print(f"[DEBUG] data_resp record count: {len(data_resp.get('records', []))}")
        
        if "columnMetadata" not in data_resp:
            print("[ERROR] Data query response missing columnMetadata")
            return err("Database query response format error: missing column metadata", status=500)

    except ConnectionError as e:
        print(f"[ERROR] Database connection error: {e}")
        print(traceback.format_exc())
        return err(f"Database connection failed: {str(e)}", status=503)
    except ValueError as e:
        print(f"[ERROR] Database response parsing error: {e}")
        print(traceback.format_exc())
        return err(f"Failed to parse database response: {str(e)}", status=500)
    except KeyError as e:
        print(f"[ERROR] Missing expected field in database response: {e}")
        print(traceback.format_exc())
        return err(f"Database response missing expected field: {str(e)}", status=500)
    except Exception as e:
        print(f"[ERROR] Unexpected database error: {e}")
        print(traceback.format_exc())
        return err(f"Database query failed: {str(e)}", status=500)

    products = rows_to_dicts(data_resp["columnMetadata"], data_resp["records"])
    total_pages = max(1, -(-total_items // page_size))

    return ok({
        "data": products,
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