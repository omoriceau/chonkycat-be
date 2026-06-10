"""
orders/service.py

OrderService handles:
  - Product lookup + stock validation
  - Promotion code resolution
  - Tax + shipping calculation
  - Order + order_items DB writes
  - connection_id persistence (for WebSocket callback)
  - EventBridge: OrderCreated (→ Payment Lambda)
  - EventBridge: LowStockDetected (→ Email Lambda) when items cross threshold
"""

import json
import logging
import os
from decimal import Decimal, ROUND_HALF_UP

import boto3
from botocore.exceptions import ClientError

from shared.events import (
    SOURCE_ORDERS,
    ORDER_CREATED,
    LOW_STOCK_DETECTED,
)

from orders.models import (
    CreateOrderRequest,
    ResolvedOrder,
    ResolvedOrderItem,
    ValidationError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants  (externalise to env / config table as needed)
# ---------------------------------------------------------------------------

TAX_RATE         = Decimal("0.13")   # Ontario HST
FREE_SHIP_ABOVE  = Decimal("75.00")
FLAT_SHIP_FEE    = Decimal("10.00")

EVENTBRIDGE_BUS = os.environ.get("EVENT_BUS_NAME", "chonkychonk-bus")


def _cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class OrderService:

    def __init__(
        self,
        db_cluster_arn: str,
        db_secret_arn: str,
        db_name: str,
        rds_client=None,
        events_client=None,
    ):
        self._cluster_arn = db_cluster_arn
        self._secret_arn  = db_secret_arn
        self._db_name     = db_name
        self._rds         = rds_client    or boto3.client("rds-data")
        self._events      = events_client or boto3.client("events")

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def create_order(self, request: CreateOrderRequest) -> dict:
        """
        Full order creation flow.
        Returns a summary dict (order_id, total, status).
        Raises ValidationError for bad input, ClientError for infra failures.
        """
        # 1. Resolve products + validate stock
        resolved_items = self._resolve_items(request.items)

        # 2. Resolve promotion (optional)
        promotion_id, discount = self._resolve_promotion(request.promotion_code, resolved_items)

        # 3. Calculate totals
        subtotal        = _cents(sum(i.line_total for i in resolved_items))
        discounted_sub  = _cents(max(Decimal("0"), subtotal - discount))
        shipping_amount = Decimal("0") if discounted_sub >= FREE_SHIP_ABOVE else FLAT_SHIP_FEE
        tax_amount      = _cents(discounted_sub * TAX_RATE)
        total_amount    = _cents(discounted_sub + tax_amount + shipping_amount)

        resolved = ResolvedOrder(
            user_id          = request.user_id,
            customer_email   = request.customer_email,
            items            = resolved_items,
            shipping         = request.shipping,
            subtotal         = subtotal,
            tax_amount       = tax_amount,
            shipping_amount  = shipping_amount,
            discount_amount  = discount,
            total_amount     = total_amount,
            promotion_id     = promotion_id,
            promotion_code   = request.promotion_code,
            customer_notes   = request.customer_notes,
            currency         = request.currency,
            connection_id    = request.connection_id,
            payment_provider = request.payment_provider,
        )

        # 4. Persist order
        order_id = self._insert_order(resolved)

        # 5. Emit OrderCreated → Payment Lambda
        self._emit_order_created(order_id, resolved)

        # 6. Emit LowStockDetected for any products that crossed the threshold
        self._emit_low_stock_if_needed(resolved.items)

        return {
            "order_id":     order_id,
            "subtotal":     str(subtotal),
            "discount":     str(discount),
            "tax":          str(tax_amount),
            "shipping":     str(shipping_amount),
            "total":        str(total_amount),
            "currency":     request.currency,
            "status":       "pending",
        }

    # ------------------------------------------------------------------
    # Product resolution
    # ------------------------------------------------------------------

    def _resolve_items(self, items) -> list[ResolvedOrderItem]:
        product_ids = [i.product_id for i in items]

        # Fetch all requested products in one query
        placeholders = ", ".join(f":pid{n}" for n in range(len(product_ids)))
        sql = f"""
            SELECT id, name, price, qty, active
            FROM   products
            WHERE  id IN ({placeholders})
        """
        params = [
            {"name": f"pid{n}", "value": {"longValue": pid}}
            for n, pid in enumerate(product_ids)
        ]
        resp = self._execute(sql, params, "resolve_products", include_metadata=True)

        product_map = {}
        for row in self._to_dicts(resp["columnMetadata"], resp["records"]):
            product_map[row["id"]] = row

        resolved = []
        for item in items:
            product = product_map.get(item.product_id)
            if not product:
                raise ValidationError(f"Product {item.product_id} not found")
            if not product["active"]:
                raise ValidationError(f"Product {item.product_id} is not available")
            if product["qty"] < item.quantity:
                raise ValidationError(
                    f"Insufficient stock for '{product['name']}' "
                    f"(requested {item.quantity}, available {product['qty']})"
                )
            unit_price = Decimal(str(product["price"]))
            resolved.append(ResolvedOrderItem(
                product_id    = item.product_id,
                name_snapshot = product["name"],
                quantity      = item.quantity,
                unit_price    = unit_price,
                line_total    = _cents(unit_price * item.quantity),
            ))

        return resolved

    # ------------------------------------------------------------------
    # Promotion resolution
    # ------------------------------------------------------------------

    def _resolve_promotion(
        self, code: str | None, items: list[ResolvedOrderItem]
    ) -> tuple[int | None, Decimal]:
        if not code:
            return None, Decimal("0")

        sql = """
            SELECT id, discount_type, discount_value, active, expires_at
            FROM   promotions
            WHERE  code = :code
            LIMIT  1
        """
        resp = self._execute(
            sql,
            [{"name": "code", "value": {"stringValue": code.strip().upper()}}],
            "resolve_promotion",
            include_metadata=True,
        )
        rows = self._to_dicts(resp["columnMetadata"], resp["records"])

        if not rows:
            raise ValidationError(f"Promotion code '{code}' is not valid")

        promo = rows[0]
        if not promo["active"]:
            raise ValidationError(f"Promotion code '{code}' is no longer active")

        subtotal = sum(i.line_total for i in items)
        value    = Decimal(str(promo["discount_value"]))

        if promo["discount_type"] == "percentage":
            discount = _cents(subtotal * (value / 100))
        else:
            discount = _cents(min(value, subtotal))

        return promo["id"], discount

    # ------------------------------------------------------------------
    # DB writes
    # ------------------------------------------------------------------

    def _insert_order(self, o: ResolvedOrder) -> int:
        # 1. Insert order row
        sql = """
            INSERT INTO orders (
                user_id, status, subtotal, tax_amount, shipping_amount, total_amount,
                customer_notes, connection_id,
                shipping_name, shipping_address1, shipping_address2,
                shipping_city, shipping_province, shipping_postal_code, shipping_country
            ) VALUES (
                :user_id, 'pending', :subtotal, :tax, :shipping, :total,
                :notes, :connection_id,
                :s_name, :s_addr1, :s_addr2,
                :s_city, :s_prov, :s_postal, :s_country
            )
        """
        params = [
            {"name": "user_id",       "value": {"longValue":   o.user_id}},
            {"name": "subtotal",      "value": {"stringValue": str(o.subtotal)}},
            {"name": "tax",           "value": {"stringValue": str(o.tax_amount)}},
            {"name": "shipping",      "value": {"stringValue": str(o.shipping_amount)}},
            {"name": "total",         "value": {"stringValue": str(o.total_amount)}},
            {"name": "notes",         "value": {"stringValue": o.customer_notes or ""} if o.customer_notes else {"isNull": True}},
            {"name": "connection_id", "value": {"stringValue": o.connection_id  or ""} if o.connection_id  else {"isNull": True}},
            {"name": "s_name",        "value": {"stringValue": o.shipping.name}},
            {"name": "s_addr1",       "value": {"stringValue": o.shipping.address1}},
            {"name": "s_addr2",       "value": {"stringValue": o.shipping.address2 or ""} if o.shipping.address2 else {"isNull": True}},
            {"name": "s_city",        "value": {"stringValue": o.shipping.city}},
            {"name": "s_prov",        "value": {"stringValue": o.shipping.province}},
            {"name": "s_postal",      "value": {"stringValue": o.shipping.postal_code}},
            {"name": "s_country",     "value": {"stringValue": o.shipping.country}},
        ]

        resp     = self._execute(sql, params, "insert_order")
        order_id = resp["generatedFields"][0]["longValue"]

        # 2. Insert order items
        for item in o.items:
            item_sql = """
                INSERT INTO order_items
                  (order_id, product_id, quantity, unit_price, line_total, name_snapshot)
                VALUES
                  (:order_id, :product_id, :qty, :unit_price, :line_total, :name)
            """
            self._execute(item_sql, [
                {"name": "order_id",   "value": {"longValue":   order_id}},
                {"name": "product_id", "value": {"longValue":   item.product_id}},
                {"name": "qty",        "value": {"longValue":   item.quantity}},
                {"name": "unit_price", "value": {"stringValue": str(item.unit_price)}},
                {"name": "line_total", "value": {"stringValue": str(item.line_total)}},
                {"name": "name",       "value": {"stringValue": item.name_snapshot}},
            ], "insert_order_item")

        # 3. Insert order promotion (if any)
        if o.promotion_id:
            promo_sql = """
                INSERT INTO order_promotions (order_id, promotion_id, discount_amount)
                VALUES (:order_id, :promo_id, :discount)
            """
            self._execute(promo_sql, [
                {"name": "order_id",  "value": {"longValue":   order_id}},
                {"name": "promo_id",  "value": {"longValue":   o.promotion_id}},
                {"name": "discount",  "value": {"stringValue": str(o.discount_amount)}},
            ], "insert_order_promotion")

        return order_id

    # ------------------------------------------------------------------
    # EventBridge
    # ------------------------------------------------------------------

    def _emit_order_created(self, order_id: int, o: ResolvedOrder) -> None:
        """
        Fires an OrderCreated event.  The Payment Lambda listens on this bus
        filtered to source=chonkychonk.orders, detail-type=OrderCreated.
        """
        shipping_address = ", ".join(filter(None, [
            o.shipping.address1,
            o.shipping.address2,
            o.shipping.city,
            o.shipping.province,
            o.shipping.postal_code,
            o.shipping.country,
        ]))

        detail = {
            # Payment Lambda routing
            "event_type":      "charge",
            "order_id":        order_id,
            "amount":          str(o.total_amount),
            "currency":        o.currency,
            "customer_email":  o.customer_email,
            "provider":        o.payment_provider,
            "description":     f"ChonkyChonk order #{order_id}",
            "connection_id":   o.connection_id,

            # Email template fields — passed through so the Payment Lambda
            # can send the confirmation/failure email without a DB round-trip
            "subtotal":        str(o.subtotal),
            "discount":        str(o.discount_amount),
            "tax":             str(o.tax_amount),
            "shipping_fee":    str(o.shipping_amount),
            "promotion_code":  o.promotion_code,
            "shipping_name":   o.shipping.name,
            "shipping_address": shipping_address,
            "items": [
                {
                    "name":       item.name_snapshot,
                    "quantity":   item.quantity,
                    "unit_price": str(item.unit_price),
                    "line_total": str(item.line_total),
                }
                for item in o.items
            ],
        }
        try:
            self._events.put_events(Entries=[{
                "Source":       SOURCE_ORDERS,
                "DetailType":   ORDER_CREATED,
                "Detail":       json.dumps(detail),
                "EventBusName": EVENTBRIDGE_BUS,
            }])
            logger.info("EventBridge OrderCreated emitted | order=%s", order_id)
        except ClientError as e:
            # Log but don't fail the order — a dead-letter / retry policy on
            # EventBridge should handle redelivery
            logger.error("Failed to emit OrderCreated event: %s", e)

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _emit_low_stock_if_needed(self, items: list) -> None:
        """
        After an order is saved, check if any ordered products have dropped
        to or below their low stock threshold. If so, emit LowStockDetected.
        """
        product_ids   = [i.product_id for i in items]
        placeholders  = ", ".join(f":pid{n}" for n in range(len(product_ids)))
        sql = f"""
            SELECT id, sku, name, category, qty AS current_stock, low_stock_threshold
            FROM   products
            WHERE  id IN ({placeholders})
            AND    qty <= low_stock_threshold
        """
        params = [
            {"name": f"pid{n}", "value": {"longValue": pid}}
            for n, pid in enumerate(product_ids)
        ]
        try:
            resp = self._execute(sql, params, "low_stock_check", include_metadata=True)
            low  = self._to_dicts(resp["columnMetadata"], resp["records"])
        except ClientError:
            logger.error("Low stock check query failed — skipping emit")
            return

        if not low:
            return

        logger.info("Low stock detected for %d product(s)", len(low))
        detail = {"products": [
            {
                "product_id":    p["id"],
                "sku":           p["sku"],
                "name":          p["name"],
                "category":      p["category"],
                "current_stock": p["current_stock"],
                "threshold":     p["low_stock_threshold"],
            }
            for p in low
        ]}
        try:
            self._events.put_events(Entries=[{
                "Source":       SOURCE_ORDERS,
                "DetailType":   LOW_STOCK_DETECTED,
                "Detail":       json.dumps(detail),
                "EventBusName": EVENTBRIDGE_BUS,
            }])
            logger.info("LowStockDetected emitted for %d product(s)", len(low))
        except ClientError as e:
            logger.error("Failed to emit LowStockDetected: %s", e)

    def _execute(self, sql: str, params: list, label: str, include_metadata: bool = False) -> dict:
        try:
            return self._rds.execute_statement(
                resourceArn=self._cluster_arn,
                secretArn=self._secret_arn,
                database=self._db_name,
                sql=sql,
                parameters=params,
                includeResultMetadata=include_metadata,
            )
        except ClientError as e:
            logger.error("DB error [%s]: %s", label, e)
            raise

    @staticmethod
    def _to_dicts(column_metadata: list, records: list) -> list[dict]:
        columns = [col["name"] for col in column_metadata]
        result  = []
        for record in records:
            row = {}
            for col, field in zip(columns, record):
                row[col] = next(iter(field.values())) if field != {"isNull": True} else None
            result.append(row)
        return result
