"""
DynamoDB helper for the products Lambda.

Replaces the old psycopg2/RDS client. Auth is via the Lambda's execution
role (IAM) — no secrets/passwords needed, unlike the RDS version.

Table: aws_dynamodb_table.products (see terraform)
  hash_key: product_id (S)
  GSI CategoryIndex: hash=category, range=name          -> browse by category, sorted by name
  GSI ReorderIndex:  hash=reorder_flag, range=product_id -> sparse, low-stock items only

Soft delete: mirrors the original Postgres `deleted_at` column. Deleted
items are never removed from the table — `deleted_at` is set instead —
and every listing method excludes them unless `include_deleted=True` is
passed, matching the old `WHERE deleted_at IS NULL` default.
"""

import os
import boto3
from boto3.dynamodb.conditions import Key, Attr


def _not_deleted() -> Attr:
    return Attr("deleted_at").not_exists()


class DynamoDBClient:
    """Thin wrapper around a boto3 Table resource for the products table."""

    def __init__(self):
        table_name = os.environ.get("PRODUCTS_TABLE_NAME")
        if not table_name:
            raise RuntimeError(
                "Missing required environment variable: PRODUCTS_TABLE_NAME. "
                "Set this to the products table name output by terraform "
                "(aws_dynamodb_table.products.name)."
            )
        region = os.environ.get("AWS_REGION")  # set automatically by Lambda
        resource = boto3.resource("dynamodb", region_name=region)
        self.table = resource.Table(table_name)

    # ------------------------------------------------------------------
    # Single item
    # ------------------------------------------------------------------

    def get_product(self, product_id: str) -> dict | None:
        resp = self.table.get_item(Key={"product_id": str(product_id)})
        return resp.get("Item")

    # ------------------------------------------------------------------
    # Listing helpers — each returns a plain list of items.
    # Filtering/pagination/sorting across these is handled in the read
    # handler since it differs depending on which index was used.
    # ------------------------------------------------------------------

    def query_by_category(self, category: str, active_only: bool, include_deleted: bool = False) -> list[dict]:
        """Query CategoryIndex — items already sorted by name ascending."""
        kwargs = {
            "IndexName": "CategoryIndex",
            "KeyConditionExpression": Key("category").eq(category),
        }
        filt = _build_filter(active_only, include_deleted)
        if filt is not None:
            kwargs["FilterExpression"] = filt

        items = []
        while True:
            resp = self.table.query(**kwargs)
            items.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return items

    def query_low_stock(self, active_only: bool, include_deleted: bool = False) -> list[dict]:
        """Query the sparse ReorderIndex — only items currently at/under threshold."""
        kwargs = {
            "IndexName": "ReorderIndex",
            "KeyConditionExpression": Key("reorder_flag").eq("true"),
        }
        filt = _build_filter(active_only, include_deleted)
        if filt is not None:
            kwargs["FilterExpression"] = filt

        items = []
        while True:
            resp = self.table.query(**kwargs)
            items.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return items

    def scan_all(self, active_only: bool, include_deleted: bool = False) -> list[dict]:
        """
        Full table scan — used only when no category or low_stock filter is
        given, i.e. "list everything". There is no index that spans all
        categories, so this is the unavoidable option for that case.
        """
        kwargs = {}
        filt = _build_filter(active_only, include_deleted)
        if filt is not None:
            kwargs["FilterExpression"] = filt

        items = []
        while True:
            resp = self.table.scan(**kwargs)
            items.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return items

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create_product(self, item: dict) -> dict:
        """Put a brand-new product. Fails if product_id already exists
        (defends against ULID collisions / accidental double-submits)."""
        self.table.put_item(
            Item=item,
            ConditionExpression=Attr("product_id").not_exists(),
        )
        return item

    def get_product_by_sku(self, sku: str) -> dict | None:
        """Used only for the create/update-time SKU-uniqueness check. Not
        indexed, so this is a scan — fine at catalog scale (few thousand
        items) but would want a SkuIndex GSI if the catalog grows a lot.
        Deliberately does NOT exclude soft-deleted items: a deleted
        product's SKU should still be treated as taken (avoids two items
        sharing a SKU if the deleted one is ever restored)."""
        resp = self.table.scan(FilterExpression=Attr("sku").eq(sku))
        items = resp.get("Items", [])
        return items[0] if items else None

    def get_products_by_skus(self, skus: list[str]) -> dict[str, dict]:
        """Batch SKU lookup for the inventory-check endpoint. Not indexed
        (same caveat as get_product_by_sku above), so this scans filtered to
        the requested SKUs via IN. DynamoDB's IN operator caps at 100 values
        per expression, so requests are chunked. Deliberately does NOT
        exclude soft-deleted items — the caller decides how to treat those."""
        skus = list(dict.fromkeys(skus))  # de-dupe, preserve order
        found: dict[str, dict] = {}
        for i in range(0, len(skus), 100):
            chunk = skus[i:i + 100]
            kwargs = {"FilterExpression": Attr("sku").is_in(chunk)}
            while True:
                resp = self.table.scan(**kwargs)
                for item in resp.get("Items", []):
                    found[item["sku"]] = item
                last_key = resp.get("LastEvaluatedKey")
                if not last_key:
                    break
                kwargs["ExclusiveStartKey"] = last_key
        return found

    def update_product(self, product_id: str, updates: dict, remove_attrs: list[str]) -> dict:
        """Partial update via UpdateItem. `updates` is a dict of attribute
        name -> new value to SET. `remove_attrs` is a list of attribute
        names to REMOVE entirely (used for the sparse reorder_flag when an
        item is no longer low-stock, and for restoring a soft-deleted item
        by removing `deleted_at` — either way the attribute must be
        absent, not null/false, to keep sparse-index and IS-NULL-style
        semantics correct)."""
        set_parts = []
        remove_parts = []
        expr_names = {}
        expr_values = {}

        for i, (k, v) in enumerate(updates.items()):
            name_ph = f"#f{i}"
            val_ph = f":v{i}"
            expr_names[name_ph] = k
            expr_values[val_ph] = v
            set_parts.append(f"{name_ph} = {val_ph}")

        for j, k in enumerate(remove_attrs):
            name_ph = f"#r{j}"
            expr_names[name_ph] = k
            remove_parts.append(name_ph)

        expression = ""
        if set_parts:
            expression += "SET " + ", ".join(set_parts)
        if remove_parts:
            expression += (" " if expression else "") + "REMOVE " + ", ".join(remove_parts)

        kwargs = {
            "Key": {"product_id": str(product_id)},
            "UpdateExpression": expression,
            "ExpressionAttributeNames": expr_names,
            "ConditionExpression": Attr("product_id").exists(),
            "ReturnValues": "ALL_NEW",
        }
        if expr_values:
            kwargs["ExpressionAttributeValues"] = expr_values

        resp = self.table.update_item(**kwargs)
        return resp.get("Attributes", {})

    def soft_delete_product(self, product_id: str, deleted_at: str) -> dict:
        """Set deleted_at (mirrors the old `UPDATE ... SET deleted_at = now()`).
        The item is left in the table — GET-by-id, restore, and reporting
        against historical orders all still work. Fails if the product
        doesn't exist."""
        return self.update_product(product_id, {"deleted_at": deleted_at, "updated_at": deleted_at}, [])

    def restore_product(self, product_id: str, updated_at: str) -> dict:
        """Undo a soft delete by removing `deleted_at` entirely."""
        return self.update_product(product_id, {"updated_at": updated_at}, ["deleted_at"])

    def hard_delete_product(self, product_id: str) -> None:
        """Permanently remove the item. Not exposed via the API by default —
        soft delete is the standard path — but kept available for an admin
        'purge' action later. Fails if the product doesn't exist."""
        self.table.delete_item(
            Key={"product_id": str(product_id)},
            ConditionExpression=Attr("product_id").exists(),
        )


def _build_filter(active_only: bool, include_deleted: bool):
    filt = None
    if active_only:
        filt = Attr("active").eq(True)
    if not include_deleted:
        filt = _not_deleted() if filt is None else (filt & _not_deleted())
    return filt


def get_db_client() -> DynamoDBClient:
    return DynamoDBClient()