"""
DynamoDB helper for the orders Lambda.

Replaces the old psycopg2/RDS client entirely — auth is via the Lambda's
execution role (IAM). This lambda touches THREE tables:

  orders      (aws_dynamodb_table.orders)      — read/write
  products    (aws_dynamodb_table.products)    — read (stock check) + write (decrement on order)
  promotions  (aws_dynamodb_table.promotions)  — read only

ORDERS TABLE — single-table layout (see terraform comments)
  hash_key: order_id (S)   range_key: sk (S)
    sk = "ORDER"          -> main order record (subtotal/tax/shipping/total,
                             shipping address, status, applied_promotions list)
    sk = "ITEM#<0001..>"  -> order line items (product_id, qty, unit_price, ...)
    sk = "TRACKING#<ts>"  -> written by some other (fulfillment) lambda, not
                             this one — get_order_with_children() still
                             returns them if present, for forward-compat.
  GSIs UserOrdersIndex / StatusIndex are sparse: only the "ORDER" sk item
  carries user_id/status/created_at, so children never appear in them.

STOCK DECREMENT — IMPORTANT
----------------------------
The original Postgres code never issued an `UPDATE products SET qty = ...`
anywhere in Python — grepping the whole codebase turns up no decrement at
all. That almost certainly means stock was decremented by a DB trigger on
`order_items` INSERT that lived in the Postgres schema/migrations, which
weren't included in what was shared with me. DynamoDB has no equivalent
trigger mechanism, so `create_order_transaction` below decrements stock
explicitly, conditioned on sufficient stock being available
(`qty >= :requested`), as part of the same transaction that creates the
order. If the original trigger did anything more elaborate than a plain
decrement, that behavior needs to be ported in here too.
"""

import os
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

_serializer = TypeSerializer()


def _to_dynamo(item: dict) -> dict:
    return {k: _serializer.serialize(v) for k, v in item.items()}


class InsufficientStock(Exception):
    def __init__(self, product_id):
        self.product_id = product_id
        super().__init__(f"Insufficient stock for product {product_id}")


class OrderNotFound(Exception):
    pass


class DynamoDBClient:

    def __init__(self):
        self.orders_table_name = os.environ.get("ORDERS_TABLE_NAME")
        self.products_table_name = os.environ.get("PRODUCTS_TABLE_NAME")
        self.promotions_table_name = os.environ.get("PROMOTIONS_TABLE_NAME")

        missing = [
            name for name, val in (
                ("ORDERS_TABLE_NAME", self.orders_table_name),
                ("PRODUCTS_TABLE_NAME", self.products_table_name),
                ("PROMOTIONS_TABLE_NAME", self.promotions_table_name),
            ) if not val
        ]
        if missing:
            raise RuntimeError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                f"Set these to the table names output by terraform."
            )

        region = os.environ.get("AWS_REGION")
        self._resource = boto3.resource("dynamodb", region_name=region)
        self._client = self._resource.meta.client  # low-level client for transactions

        self.orders_table = self._resource.Table(self.orders_table_name)
        self.products_table = self._resource.Table(self.products_table_name)
        self.promotions_table = self._resource.Table(self.promotions_table_name)

    # ------------------------------------------------------------------
    # Orders — reads
    # ------------------------------------------------------------------

    def get_order_with_children(self, order_id: str) -> dict | None:
        """
        One Query against the orders table returns the ORDER record plus
        every ITEM# / TRACKING# child in a single round trip.
        Returns None if there's no ORDER record for this order_id.
        """
        resp = self.orders_table.query(
            KeyConditionExpression=Key("order_id").eq(order_id)
        )
        rows = resp.get("Items", [])
        order = next((r for r in rows if r["sk"] == "ORDER"), None)
        if order is None:
            return None

        items = sorted((r for r in rows if r["sk"].startswith("ITEM#")), key=lambda r: r["sk"])
        tracking = sorted((r for r in rows if r["sk"].startswith("TRACKING#")), key=lambda r: r["sk"])
        return {"order": order, "items": items, "tracking": tracking}

    # ------------------------------------------------------------------
    # Orders — writes
    # ------------------------------------------------------------------

    def create_order_transaction(
        self,
        order_item: dict,
        item_children: list[dict],
        stock_decrements: list[dict],
    ) -> None:
        """
        Atomically: Put the ORDER item, Put every ITEM# child, and decrement
        stock on every ordered product (conditioned on enough stock being
        available). All-or-nothing — if any product no longer has enough
        stock (e.g. a race with another order), the whole order is rolled
        back and nothing is written.

        stock_decrements: [{"product_id": str, "quantity": int}, ...]
        """
        transact_items = [
            {
                "Put": {
                    "TableName": self.orders_table_name,
                    "Item": _to_dynamo(order_item),
                    "ConditionExpression": "attribute_not_exists(order_id)",
                }
            }
        ]
        for child in item_children:
            transact_items.append({
                "Put": {
                    "TableName": self.orders_table_name,
                    "Item": _to_dynamo(child),
                }
            })
        for dec in stock_decrements:
            transact_items.append({
                "Update": {
                    "TableName": self.products_table_name,
                    "Key": _to_dynamo({"product_id": dec["product_id"]}),
                    "UpdateExpression": "SET qty = qty - :q",
                    "ConditionExpression": "qty >= :q",
                    "ExpressionAttributeValues": _to_dynamo({":q": dec["quantity"]}),
                }
            })

        try:
            self._client.transact_write_items(TransactItems=transact_items)
        except ClientError as e:
            if e.response["Error"]["Code"] == "TransactionCanceledException":
                # We can't cheaply tell *which* product failed from the
                # cancellation reasons without matching them positionally;
                # the caller already validated stock right before this call,
                # so this only fires on a genuine race — surface it generically.
                raise InsufficientStock("one or more ordered products") from e
            raise

    def soft_delete_order(self, order_id: str) -> bool:
        """Set deleted_at on the ORDER item. Returns False if not found or already deleted."""
        try:
            self.orders_table.update_item(
                Key={"order_id": order_id, "sk": "ORDER"},
                UpdateExpression="SET deleted_at = :now",
                ConditionExpression="attribute_exists(order_id) AND attribute_not_exists(deleted_at)",
                ExpressionAttributeValues={":now": _now_iso()},
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def update_order_transaction(
        self,
        order_id: str,
        order_updates: dict,
        old_item_sks: list[str],
        new_item_children: list[dict] | None,
    ) -> None:
        """
        Update the ORDER item's attributes and, if new_item_children is
        provided, replace all ITEM# children (delete old, put new) — all in
        one transaction.
        """
        update_expr_parts = []
        expr_names = {}
        expr_values = {}
        for i, (k, v) in enumerate(order_updates.items()):
            name_ph = f"#f{i}"
            value_ph = f":v{i}"
            update_expr_parts.append(f"{name_ph} = {value_ph}")
            expr_names[name_ph] = k
            expr_values[value_ph] = v

        transact_items = [
            {
                "Update": {
                    "TableName": self.orders_table_name,
                    "Key": _to_dynamo({"order_id": order_id, "sk": "ORDER"}),
                    "UpdateExpression": "SET " + ", ".join(update_expr_parts),
                    "ExpressionAttributeNames": expr_names,
                    "ExpressionAttributeValues": _to_dynamo(expr_values),
                    "ConditionExpression": "attribute_exists(order_id)",
                }
            }
        ]

        if new_item_children is not None:
            for sk in old_item_sks:
                transact_items.append({
                    "Delete": {
                        "TableName": self.orders_table_name,
                        "Key": _to_dynamo({"order_id": order_id, "sk": sk}),
                    }
                })
            for child in new_item_children:
                transact_items.append({
                    "Put": {
                        "TableName": self.orders_table_name,
                        "Item": _to_dynamo(child),
                    }
                })

        self._client.transact_write_items(TransactItems=transact_items)

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------

    def batch_get_products(self, product_ids: list[str]) -> dict[str, dict]:
        """Returns {product_id: item} for whichever of the given ids exist."""
        if not product_ids:
            return {}

        result = {}
        # BatchGetItem caps at 100 keys per call.
        for i in range(0, len(product_ids), 100):
            chunk = product_ids[i:i + 100]
            request_items = {
                self.products_table_name: {
                    "Keys": [{"product_id": pid} for pid in chunk]
                }
            }
            while request_items:
                resp = self._resource.batch_get_item(RequestItems=request_items)
                for item in resp["Responses"].get(self.products_table_name, []):
                    result[item["product_id"]] = item
                request_items = resp.get("UnprocessedKeys") or {}
        return result

    def update_product_reorder_state(self, product_id: str, current_qty, threshold) -> None:
        """
        Maintain the sparse ReorderIndex: set reorder_flag="true" once a
        product is at/under its low-stock threshold, remove it once
        restocked above threshold. Not part of the stock-decrement
        transaction on purpose — this is a secondary, eventually-consistent
        signal for the reorder report, not something order correctness
        depends on.
        """
        if current_qty <= threshold:
            self.products_table.update_item(
                Key={"product_id": product_id},
                UpdateExpression="SET reorder_flag = :flag",
                ExpressionAttributeValues={":flag": "true"},
            )
        else:
            self.products_table.update_item(
                Key={"product_id": product_id},
                UpdateExpression="REMOVE reorder_flag",
            )

    # ------------------------------------------------------------------
    # Promotions
    # ------------------------------------------------------------------

    def get_promotion(self, code: str) -> dict | None:
        resp = self.promotions_table.get_item(Key={"code": code})
        return resp.get("Item")


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def get_db_client() -> DynamoDBClient:
    return DynamoDBClient()
