"""
Lambda: GET /products
Query Parameters:
  - show_all    (bool, default false) — include inactive products
  - page        (int,  default 1)     — page number
  - page_size   (int,  default 100, max 1000) — items per page
  - category    (str,  optional)      — filter by category
  - low_stock   (bool, default false) — only return low-stock items

Environment Variables:
  - DB_CLUSTER_ARN   Aurora cluster ARN
  - DB_SECRET_ARN    Secrets Manager secret ARN (DB credentials)
  - DB_NAME          Database name (chonkychonk)
"""

import json
import os
from botocore.exceptions import ClientError

# Import DB helper (uses local MySQL or RDS based on environment)
from shared.db import get_db_client

rds = get_db_client()

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

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


def ok(body: dict, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def err(message: str, status: int = 400) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": message}),
    }


def rows_to_dicts(column_metadata: list, records: list) -> list[dict]:
    """Convert RDS Data API response records into a list of dicts."""
    columns = [col["name"] for col in column_metadata]
    result = []
    for record in records:
        row = {}
        for col, field in zip(columns, record):
            # Each field is a dict with one key indicating the type
            value = next(iter(field.values())) if field != {"isNull": True} else None
            row[col] = value
        result.append(row)
    return result


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:
    params = event.get("queryStringParameters") or {}

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

    # -- Build WHERE clauses & parameter list --------------------------------
    conditions   = []
    sql_params   = []

    if not show_all:
        conditions.append("p.active = :active")
        sql_params.append({"name": "active", "value": {"longValue": 1}})

    if category:
        conditions.append("p.category = :category")
        sql_params.append({"name": "category", "value": {"stringValue": category}})

    if low_stock:
        conditions.append("p.qty <= p.low_stock_threshold")

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    # -- Count query (for pagination metadata) --------------------------------
    count_sql = f"SELECT COUNT(*) AS total FROM products p {where_clause}"

    # -- Data query -----------------------------------------------------------
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
        LIMIT  :limit
        OFFSET :offset
    """

    # Pagination params are appended after the shared filter params
    paginated_params = sql_params + [
        {"name": "limit",  "value": {"longValue": page_size}},
        {"name": "offset", "value": {"longValue": offset}},
    ]

    # -- Execute --------------------------------------------------------------
    try:
        count_resp = rds.execute_statement(
            resourceArn=DB_CLUSTER_ARN,
            secretArn=DB_SECRET_ARN,
            database=DB_NAME,
            sql=count_sql,
            parameters=sql_params,
        )
        total_items = count_resp["records"][0][0]["longValue"]

        data_resp = rds.execute_statement(
            resourceArn=DB_CLUSTER_ARN,
            secretArn=DB_SECRET_ARN,
            database=DB_NAME,
            sql=data_sql,
            parameters=paginated_params,
            includeResultMetadata=True,
        )

    except ClientError as e:
        print(f"RDS Data API error: {e}")
        return err("Database error", status=500)

    products = rows_to_dicts(data_resp["columnMetadata"], data_resp["records"])
    total_pages = max(1, -(-total_items // page_size))  # ceiling division

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
