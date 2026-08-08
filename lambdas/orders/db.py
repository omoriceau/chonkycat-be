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

import logging
import os
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

_serializer = TypeSerializer()


def _log_transaction_cancellation(e: ClientError, context: str) -> None:
    """
    TransactWriteItems failures only carry per-item detail (which write was
    rejected and why) in response['CancellationReasons'] — str(e) just
    lists the bare codes, e.g. "[ValidationError, None, None]", which isn't
    enough to tell which item or attribute was invalid.
    """
    reasons = e.response.get("CancellationReasons")
    if reasons:
        logger.error("%s: %s | CancellationReasons=%s", context, e, reasons)
    else:
        logger.error("%s: %s", context, e)


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
        # A plain client, NOT self._resource.meta.client: the resource
        # registers a before-parameter-build hook on its shared client that
        # auto-serializes any 'AttributeValue'-shaped field on every call
        # made through it, resource-level or not. create_order_transaction /
        # update_order_transaction below pre-serialize Items themselves
        # (via _to_dynamo) for TransactWriteItems, which the low-level API
        # requires in raw {"S": ...} form — reusing the resource's client
        # would run that hook too and double-serialize every value (e.g.
        # order_id: {"S": "x"} -> {"M": {"S": {"S": "x"}}}), which DynamoDB
        # then rejects as a key-type mismatch.
        self._client = boto3.client("dynamodb", region_name=region)

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

    SCAN_PAGE_SIZE = 100

    def scan_all_orders(self) -> list[dict]:
        """
        Full table scan — returns every row (ORDER records AND their ITEM#/
        TRACKING# children) across every order. Neither GSI spans "all
        orders" (UserOrdersIndex is per-user, StatusIndex is per-status), so
        this is the unavoidable option for a flat admin list — same
        tradeoff products/db.py's scan_all already accepts at catalog scale.
        Callers are expected to split ORDER rows from children themselves
        (see OrderService.list_orders), the same way get_order_with_children
        does for a single order.

        Still fetches every row overall — Limit just bounds each individual
        Scan call to SCAN_PAGE_SIZE items (DynamoDB's own per-call cap is
        ~1MB, not row count) rather than one huge read per round trip.
        """
        items = []
        kwargs = {"Limit": self.SCAN_PAGE_SIZE}
        while True:
            resp = self.orders_table.scan(**kwargs)
            items.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return items

    def get_open_cart(self, user_id: str) -> dict | None:
        """
        UserOrdersIndex is sparse (only "ORDER" sk rows carry user_id), so a
        Query here can only ever return ORDER rows — filtering on status
        narrows it down to the one open cart, if any.
        """
        resp = self.orders_table.query(
            IndexName="UserOrdersIndex",
            KeyConditionExpression=Key("user_id").eq(user_id),
            FilterExpression=Attr("status").eq("cart"),
        )
        items = resp.get("Items", [])
        return items[0] if items else None

    def list_orders_for_user(self, user_id: str) -> list[dict]:
        """
        The caller's own order history (storefront profile page) — every
        placed order, newest first. Same sparse UserOrdersIndex as
        get_open_cart(), just excluding the open cart itself and any
        soft-deleted orders instead of selecting for them.
        """
        resp = self.orders_table.query(
            IndexName="UserOrdersIndex",
            KeyConditionExpression=Key("user_id").eq(user_id),
            FilterExpression=Attr("status").ne("cart") & Attr("deleted_at").not_exists(),
        )
        items = resp.get("Items", [])
        items.sort(key=lambda o: o.get("created_at") or "", reverse=True)
        return items

    # ------------------------------------------------------------------
    # Cart — writes
    #
    # Cart item children are keyed by product_id directly (sk =
    # "ITEM#<product_id>") rather than the positional "ITEM#0000" scheme
    # finalized orders use below — a cart needs "does this product already
    # have a line?" to be a cheap GetItem, not a scan of every child. Both
    # schemes share the "ITEM#" prefix so get_order_with_children() (and
    # anything else that only checks that prefix) doesn't need to care
    # which one produced a given row.
    # ------------------------------------------------------------------

    @staticmethod
    def _cart_item_key(order_id: str, product_id: str) -> dict:
        return {"order_id": order_id, "sk": f"ITEM#{product_id}"}

    def create_cart_order(self, order_item: dict) -> None:
        self.orders_table.put_item(
            Item=order_item,
            ConditionExpression="attribute_not_exists(order_id)",
        )

    def get_cart_item(self, order_id: str, product_id: str) -> dict | None:
        resp = self.orders_table.get_item(Key=self._cart_item_key(order_id, product_id))
        return resp.get("Item")

    def put_cart_item(self, item: dict) -> None:
        self.orders_table.put_item(Item=item)

    def delete_cart_item(self, order_id: str, product_id: str) -> None:
        self.orders_table.delete_item(Key=self._cart_item_key(order_id, product_id))

    def touch_cart_order(self, order_id: str) -> None:
        self.orders_table.update_item(
            Key={"order_id": order_id, "sk": "ORDER"},
            UpdateExpression="SET updated_at = :now",
            ExpressionAttributeValues={":now": _now_iso()},
        )

    def reassign_cart_owner(self, order_id: str, new_user_id: str) -> None:
        self.orders_table.update_item(
            Key={"order_id": order_id, "sk": "ORDER"},
            UpdateExpression="SET user_id = :uid, updated_at = :now",
            ConditionExpression="attribute_exists(order_id)",
            ExpressionAttributeValues={":uid": new_user_id, ":now": _now_iso()},
        )

    def delete_order_with_items(self, order_id: str, item_sks: list[str]) -> None:
        """
        Delete the ORDER row and its ITEM# children. Not transactional —
        this only ever runs against an already-merged, about-to-be-discarded
        guest cart (see OrderService.claim_guest_cart), so a partial failure
        just leaves a harmless orphaned row behind rather than risking any
        real inconsistency.
        """
        self.orders_table.delete_item(Key={"order_id": order_id, "sk": "ORDER"})
        for sk in item_sks:
            self.orders_table.delete_item(Key={"order_id": order_id, "sk": sk})

    def finalize_cart_order(self, order_id: str, order_updates: dict) -> None:
        """
        Plain (non-transactional) update of the ORDER row's attributes —
        used by cart checkout, which never needs to touch item children in
        the same write (they're already correct from cart-building, unlike
        update_order_transaction()'s callers, which can replace them). The
        status="cart" condition doubles as an optimistic-concurrency guard
        against a cart being checked out twice concurrently — raises
        ClientError(ConditionalCheckFailedException) if it's already been
        claimed by another checkout.
        """
        update_expr_parts = []
        expr_names = {"#cart_status": "status"}
        expr_values = {":cart_status": "cart"}
        for i, (k, v) in enumerate(order_updates.items()):
            name_ph = f"#f{i}"
            value_ph = f":v{i}"
            update_expr_parts.append(f"{name_ph} = {value_ph}")
            expr_names[name_ph] = k
            expr_values[value_ph] = v

        self.orders_table.update_item(
            Key={"order_id": order_id, "sk": "ORDER"},
            UpdateExpression="SET " + ", ".join(update_expr_parts),
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
            ConditionExpression="attribute_exists(order_id) AND #cart_status = :cart_status",
        )

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
        
        # Decrement stock separately after order is written (not in same transaction)
        # to avoid UpdateExpression validation issues with DynamoDB low-level client
        try:
            self._client.transact_write_items(TransactItems=transact_items)
        except ClientError as e:
            if e.response["Error"]["Code"] == "TransactionCanceledException":
                _log_transaction_cancellation(e, "create_order_transaction failed")
            raise

        # Now decrement stock non-transactionally (risk: partial decrements if Lambda fails,
        # but products table will self-correct via the periodic stock sync job)
        self.decrement_stock(stock_decrements)

    def decrement_stock(self, stock_decrements: list[dict]) -> None:
        """
        Best-effort, non-transactional stock decrement, conditioned on
        sufficient stock being available per product. Used both right after
        an order is created and when a cart is checked out — in both cases
        the order/order-items are already persisted by the time this runs,
        so a failure here logs and moves on rather than raising (stock
        inconsistency self-corrects via the periodic sync job).

        stock_decrements: [{"product_id": str, "quantity": int}, ...]
        """
        for dec in stock_decrements:
            try:
                self.products_table.update_item(
                    Key={"product_id": dec["product_id"]},
                    UpdateExpression="SET qty = qty - :q",
                    ConditionExpression="qty >= :q",
                    ExpressionAttributeValues={":q": int(dec["quantity"])},
                )
            except ClientError as e:
                if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    import logging
                    logging.warning(f"Stock race on product {dec['product_id']}: insufficient stock after order created")
                else:
                    import logging
                    logging.error(f"Error updating product {dec['product_id']}: {e}")
                # Don't raise — order is already created, let it proceed

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
            # Item sks are assigned positionally ("ITEM#0000", "ITEM#0001", ...)
            # both before and after an update, so old and new children almost
            # always share sks. A single TransactWriteItems call can't carry
            # both a Delete and a Put for the same item (DynamoDB rejects that
            # as "multiple operations on one item"), so skip the explicit
            # delete for any old sk that a new Put below will overwrite
            # anyway — only genuinely stale sks (e.g. the update shrank the
            # item count) need an explicit Delete.
            new_sks = {child["sk"] for child in new_item_children}
            for sk in old_item_sks:
                if sk in new_sks:
                    continue
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

        try:
            self._client.transact_write_items(TransactItems=transact_items)
        except ClientError as e:
            if e.response["Error"]["Code"] == "TransactionCanceledException":
                _log_transaction_cancellation(e, "update_order_transaction failed")
            raise

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
