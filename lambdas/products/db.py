"""
DynamoDB helper for the products Lambda.

Replaces the old psycopg2/RDS client. Auth is via the Lambda's execution
role (IAM) — no secrets/passwords needed, unlike the RDS version.

Table: aws_dynamodb_table.products (see terraform)
  hash_key: product_id (S)
  GSI CategoryIndex: hash=category, range=name          -> browse by category, sorted by name
  GSI ReorderIndex:  hash=reorder_flag, range=product_id -> sparse, low-stock items only
"""

import os
import boto3
from boto3.dynamodb.conditions import Key, Attr


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
    # Filtering/pagination/sorting across these is handled in lambda_handler.py
    # since it differs depending on which index was used.
    # ------------------------------------------------------------------

    def query_by_category(self, category: str, active_only: bool) -> list[dict]:
        """Query CategoryIndex — items already sorted by name ascending."""
        kwargs = {
            "IndexName": "CategoryIndex",
            "KeyConditionExpression": Key("category").eq(category),
        }
        if active_only:
            kwargs["FilterExpression"] = Attr("active").eq(True)

        items = []
        while True:
            resp = self.table.query(**kwargs)
            items.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return items

    def query_low_stock(self, active_only: bool) -> list[dict]:
        """Query the sparse ReorderIndex — only items currently at/under threshold."""
        kwargs = {
            "IndexName": "ReorderIndex",
            "KeyConditionExpression": Key("reorder_flag").eq("true"),
        }
        if active_only:
            kwargs["FilterExpression"] = Attr("active").eq(True)

        items = []
        while True:
            resp = self.table.query(**kwargs)
            items.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return items

    def scan_all(self, active_only: bool) -> list[dict]:
        """
        Full table scan — used only when no category or low_stock filter is
        given, i.e. "list everything". There is no index that spans all
        categories, so this is the unavoidable option for that case.
        """
        kwargs = {}
        if active_only:
            kwargs["FilterExpression"] = Attr("active").eq(True)

        items = []
        while True:
            resp = self.table.scan(**kwargs)
            items.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return items


def get_db_client() -> DynamoDBClient:
    return DynamoDBClient()