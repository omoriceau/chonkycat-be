"""
orders/service.py

OrderService handles:
  - Product lookup + stock validation (+ atomic decrement on create)
  - Promotion code resolution
  - Tax + shipping calculation
  - Order + order-item writes (single-table design in DynamoDB)
  - connection_id persistence (for WebSocket callback)
  - EventBridge: OrderCreated (-> Payment Lambda)
  - EventBridge: LowStockDetected (-> Email Lambda) when items cross threshold
    (also maintains the products table's sparse reorder_flag attribute)

MONEY FIELDS: stored as DynamoDB strings (not Number/Decimal) — same
representation the original RDS Data API calls used (stringValue), and it
sidesteps float/Decimal round-tripping quirks with DynamoDB's Number type.
Parsed back to Decimal on read via Decimal(str(...)), same as before.
"""

import json
import logging
import os
import uuid
from decimal import Decimal, ROUND_HALF_UP

import boto3
from botocore.exceptions import ClientError

from shared.events import (
    SOURCE_ORDERS,
    ORDER_CREATED,
    LOW_STOCK_DETECTED,
)

from models import (
    CheckoutCartRequest,
    CreateOrderRequest,
    OrderItemRequest,
    ResolvedOrder,
    ResolvedOrderItem,
    ShippingAddress,
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


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class OrderService:

    def __init__(
        self,
        db_client=None,
        events_client=None,
    ):
        from db import get_db_client, InsufficientStock
        self._db      = db_client     or get_db_client()
        self._events  = events_client or boto3.client("events")
        self._InsufficientStock = InsufficientStock

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def get_order(self, order_id: str) -> dict | None:
        """
        Retrieve a single order by ID with all its items.
        Returns a dict with order details and items, or None if not found.
        """
        result = self._db.get_order_with_children(order_id)
        if result is None:
            return None

        order = result["order"]
        if order.get("deleted_at"):
            return None

        return self._order_to_response(order, result["items"])

    def list_orders(
        self,
        page: int = 1,
        page_size: int = 50,
        status: str | None = None,
        include_deleted: bool = False,
        include_carts: bool = False,
    ) -> dict:
        """
        Admin listing — deliberately minimal per-order fields (order_id,
        user_id, status, item_count, total, created_at). No shipping
        address, customer_notes, customer_email, or payment details here;
        those stay behind the single-order GET, same as the storefront
        never gets a "browse everyone's orders" view of its own.

        Open carts (status="cart") are excluded by default — they aren't
        placed orders yet — unless include_carts is set or a caller asks
        for status="cart" explicitly.
        """
        rows = self._db.scan_all_orders()

        orders_by_id: dict[str, dict] = {}
        item_counts: dict[str, int] = {}
        for row in rows:
            if row["sk"] == "ORDER":
                orders_by_id[row["order_id"]] = row
            elif row["sk"].startswith("ITEM#"):
                item_counts[row["order_id"]] = item_counts.get(row["order_id"], 0) + 1

        show_carts = include_carts or status == "cart"
        summaries = []
        for order_id, order in orders_by_id.items():
            if order.get("deleted_at") and not include_deleted:
                continue
            if order.get("status") == "cart" and not show_carts:
                continue
            if status and order.get("status") != status:
                continue
            summaries.append({
                "order_id": order_id,
                "user_id": order.get("user_id"),
                "status": order.get("status"),
                "item_count": item_counts.get(order_id, 0),
                "total": str(order["total_amount"]) if order.get("total_amount") is not None else None,
                "created_at": order.get("created_at"),
            })

        summaries.sort(key=lambda o: o["created_at"] or "", reverse=True)

        total_items = len(summaries)
        total_pages = max(1, -(-total_items // page_size))
        start = (page - 1) * page_size
        end = start + page_size

        return {
            "data": summaries[start:end],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_items": total_items,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1,
            },
        }

    def list_my_orders(self, user_id: str) -> list[dict]:
        """
        Customer-facing order history (storefront profile page) — every
        order this user has placed, newest first, with full item/shipping
        detail (reuses _order_to_response, same shape as get_order()).
        """
        order_rows = self._db.list_orders_for_user(user_id)

        orders = []
        for row in order_rows:
            result = self._db.get_order_with_children(row["order_id"])
            if result is None:
                continue
            orders.append(self._order_to_response(result["order"], result["items"]))
        return orders

    def delete_order(self, order_id: str) -> bool:
        """
        Soft delete an order by setting a deleted_at timestamp.
        Returns True if successful, False if order not found (or already deleted).
        """
        try:
            deleted = self._db.soft_delete_order(order_id)
            if deleted:
                logger.info("Order soft deleted | order_id=%s", order_id)
            return deleted
        except Exception as e:
            logger.error("Failed to delete order %s: %s", order_id, e)
            raise

    def update_order(self, order_id: str, update: dict) -> dict | None:
        """
        Update an order with new items, shipping, notes, or promotion code.
        Only allows updates to pending orders (status='pending').
        Recalculates totals if items or promotion changes.
        Returns updated order dict, or None if order not found.
        """
        # 1. Fetch current order + items
        current_state = self._db.get_order_with_children(order_id)
        if current_state is None or current_state["order"].get("deleted_at"):
            return None

        current = current_state["order"]
        current_item_rows = current_state["items"]

        # 2. Only allow updates to pending orders
        if current["status"] != "pending":
            raise ValidationError(f"Cannot update order with status '{current['status']}'")

        # 3. Resolve updated fields
        items          = update.get("items")
        shipping       = update.get("shipping")
        customer_notes = update.get("customer_notes")
        promotion_code = update.get("promotion_code")

        # If items changed, re-resolve and recalculate
        if items is not None:
            resolved_items = self._resolve_items(items)
            subtotal = _cents(sum(i.line_total for i in resolved_items))
        else:
            resolved_items = [
                ResolvedOrderItem(
                    product_id=row["product_id"],
                    name_snapshot=row["name_snapshot"],
                    quantity=int(row["quantity"]),
                    unit_price=Decimal(str(row["unit_price"])),
                    line_total=Decimal(str(row["line_total"])),
                )
                for row in current_item_rows
            ]
            subtotal = Decimal(str(current["subtotal"]))

        # Resolve promotion
        promotion_code_resolved, discount = self._resolve_promotion(promotion_code, resolved_items)

        # Calculate new totals
        discounted_sub  = _cents(max(Decimal("0"), subtotal - discount))
        shipping_amount = Decimal("0") if discounted_sub >= FREE_SHIP_ABOVE else FLAT_SHIP_FEE
        tax_amount      = _cents(discounted_sub * TAX_RATE)
        total_amount    = _cents(discounted_sub + tax_amount + shipping_amount)

        # 4. Build the ORDER item update
        shipping_obj = shipping or ShippingAddress(
            name=current["shipping_name"],
            address1=current["shipping_address1"],
            city=current["shipping_city"],
            province=current["shipping_province"],
            postal_code=current["shipping_postal_code"],
            country=current["shipping_country"],
            address2=current.get("shipping_address2"),
        )
        notes = customer_notes if customer_notes is not None else current.get("customer_notes")

        order_updates = {
            "subtotal": str(subtotal),
            "tax_amount": str(tax_amount),
            "shipping_amount": str(shipping_amount),
            "total_amount": str(total_amount),
            "customer_notes": notes,
            "shipping_name": shipping_obj.name,
            "shipping_address1": shipping_obj.address1,
            "shipping_address2": shipping_obj.address2,
            "shipping_city": shipping_obj.city,
            "shipping_province": shipping_obj.province,
            "shipping_postal_code": shipping_obj.postal_code,
            "shipping_country": shipping_obj.country,
            "updated_at": _now_iso(),
        }
        if promotion_code is not None:
            order_updates["applied_promotions"] = (
                [{"code": promotion_code_resolved, "discount_amount": str(discount)}]
                if promotion_code_resolved else []
            )

        # 5. Build replacement ITEM# children, if items changed
        new_item_children = None
        if items is not None:
            new_item_children = [
                self._make_item_child(order_id, idx, item)
                for idx, item in enumerate(resolved_items)
            ]

        old_item_sks = [row["sk"] for row in current_item_rows]

        self._db.update_order_transaction(
            order_id=order_id,
            order_updates=order_updates,
            old_item_sks=old_item_sks,
            new_item_children=new_item_children,
        )

        logger.info("Order updated | order_id=%s", order_id)

        # 6. Return updated order
        return {
            "order_id": order_id,
            "status": "pending",
            "subtotal": str(subtotal),
            "discount": str(discount),
            "tax": str(tax_amount),
            "shipping": str(shipping_amount),
            "total": str(total_amount),
            "items": [
                {
                    "product_id": item.product_id,
                    "name": item.name_snapshot,
                    "quantity": item.quantity,
                    "unit_price": str(item.unit_price),
                    "line_total": str(item.line_total),
                }
                for item in resolved_items
            ],
        }

    def create_order(self, request: CreateOrderRequest) -> dict:
        """
        Full order creation flow.
        Returns a summary dict (order_id, total, status).
        Raises ValidationError for bad input, ClientError for infra failures.
        """
        # 1. Resolve products + validate stock
        resolved_items = self._resolve_items(request.items)

        # 2. Resolve promotion (optional)
        promotion_code_resolved, discount = self._resolve_promotion(request.promotion_code, resolved_items)

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
            promotion_id     = promotion_code_resolved,
            promotion_code   = request.promotion_code,
            customer_notes   = request.customer_notes,
            currency         = request.currency,
            connection_id    = request.connection_id,
            payment_provider = request.payment_provider,
        )

        # 4. Persist order (+ atomically decrement stock)
        order_id = self._insert_order(resolved)

        # 5. Emit OrderCreated -> Payment Lambda
        self._emit_order_created(order_id, resolved)

        # 6. Emit LowStockDetected for any products that crossed the threshold
        #    (and maintain each product's reorder_flag while we're at it)
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
    # Cart  (status="cart" ORDER rows — see identity.py for how user_id,
    # guest or logged-in, is resolved before these are called)
    # ------------------------------------------------------------------

    def get_cart(self, user_id: str) -> dict:
        cart = self._db.get_open_cart(user_id)
        if cart is None:
            return self._empty_cart_response()
        items = self._db.get_order_with_children(cart["order_id"])["items"]
        return self._cart_to_response(cart, items)

    def add_cart_item(self, user_id: str, product_id: str, quantity: int) -> dict:
        product = self._get_active_product(product_id)

        cart = self._db.get_open_cart(user_id)
        if cart is None:
            cart = self._create_cart(user_id)

        existing = self._db.get_cart_item(cart["order_id"], product_id)
        new_quantity = (int(existing["quantity"]) if existing else 0) + quantity
        self._put_cart_item(cart["order_id"], product, new_quantity)
        self._db.touch_cart_order(cart["order_id"])

        return self.get_cart(user_id)

    def update_cart_item(self, user_id: str, product_id: str, quantity: int) -> dict:
        cart = self._db.get_open_cart(user_id)
        if cart is None:
            raise ValidationError("Cart is empty")

        if quantity == 0:
            self._db.delete_cart_item(cart["order_id"], product_id)
        else:
            product = self._get_active_product(product_id)
            self._put_cart_item(cart["order_id"], product, quantity)
        self._db.touch_cart_order(cart["order_id"])

        return self.get_cart(user_id)

    def remove_cart_item(self, user_id: str, product_id: str) -> dict:
        cart = self._db.get_open_cart(user_id)
        if cart is None:
            raise ValidationError("Cart is empty")

        self._db.delete_cart_item(cart["order_id"], product_id)
        self._db.touch_cart_order(cart["order_id"])

        return self.get_cart(user_id)

    def checkout_cart(self, user_id: str, order_id: str, request: CheckoutCartRequest) -> dict:
        """
        Transitions a status="cart" order into a real, paid-for-eligible
        "pending" order in place (same order_id throughout) — validates
        stock, resolves the promotion, computes totals, decrements stock,
        and fires OrderCreated, reusing the same logic create_order() uses
        for a one-shot order.
        """
        current_state = self._db.get_order_with_children(order_id)
        if current_state is None or current_state["order"].get("user_id") != user_id:
            raise ValidationError("Cart not found")

        current = current_state["order"]
        if current["status"] != "cart":
            raise ValidationError(f"Order is not an open cart (status='{current['status']}')")

        item_rows = current_state["items"]
        if not item_rows:
            raise ValidationError("Cart is empty")

        cart_items = [
            OrderItemRequest(product_id=row["product_id"], quantity=int(row["quantity"]))
            for row in item_rows
        ]
        resolved_items = self._resolve_items(cart_items)
        promotion_code_resolved, discount = self._resolve_promotion(request.promotion_code, resolved_items)

        subtotal        = _cents(sum(i.line_total for i in resolved_items))
        discounted_sub  = _cents(max(Decimal("0"), subtotal - discount))
        shipping_amount = Decimal("0") if discounted_sub >= FREE_SHIP_ABOVE else FLAT_SHIP_FEE
        tax_amount      = _cents(discounted_sub * TAX_RATE)
        total_amount    = _cents(discounted_sub + tax_amount + shipping_amount)

        resolved = ResolvedOrder(
            user_id          = user_id,
            customer_email   = request.customer_email,
            items            = resolved_items,
            shipping         = request.shipping,
            subtotal         = subtotal,
            tax_amount       = tax_amount,
            shipping_amount  = shipping_amount,
            discount_amount  = discount,
            total_amount     = total_amount,
            promotion_id     = promotion_code_resolved,
            promotion_code   = request.promotion_code,
            customer_notes   = request.customer_notes,
            currency         = request.currency,
            connection_id    = request.connection_id,
            payment_provider = request.payment_provider,
        )

        order_updates = {
            "status": "pending",
            "subtotal": str(resolved.subtotal),
            "tax_amount": str(resolved.tax_amount),
            "shipping_amount": str(resolved.shipping_amount),
            "total_amount": str(resolved.total_amount),
            "customer_email": resolved.customer_email,
            "customer_notes": resolved.customer_notes,
            "shipping_name": resolved.shipping.name,
            "shipping_address1": resolved.shipping.address1,
            "shipping_address2": resolved.shipping.address2,
            "shipping_city": resolved.shipping.city,
            "shipping_province": resolved.shipping.province,
            "shipping_postal_code": resolved.shipping.postal_code,
            "shipping_country": resolved.shipping.country,
            "connection_id": resolved.connection_id,
            "updated_at": _now_iso(),
        }
        # Drop unset optional fields — same reasoning as _insert_order()'s
        # equivalent filter: DynamoDB would otherwise store them as
        # explicit NULL-type attributes instead of just omitting them.
        order_updates = {k: v for k, v in order_updates.items() if v is not None}
        if promotion_code_resolved:
            order_updates["applied_promotions"] = [
                {"code": promotion_code_resolved, "discount_amount": str(discount)}
            ]

        # Stock sufficiency was already checked by _resolve_items() above;
        # decrement_stock() below is a best-effort, non-transactional
        # follow-up (same as the one-shot create_order() path) — a rare
        # race there logs and self-corrects via the periodic sync job
        # rather than failing an order that's already been persisted.
        try:
            self._db.finalize_cart_order(order_id, order_updates)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValidationError("This cart has already been checked out")
            raise

        stock_decrements = [
            {"product_id": str(item.product_id), "quantity": item.quantity}
            for item in resolved.items
        ]
        self._db.decrement_stock(stock_decrements)

        self._emit_order_created(order_id, resolved)
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

    def claim_guest_cart(self, authenticated_user_id: str, guest_id: str) -> dict:
        """
        Transfers a guest's cart onto the now-authenticated user, called
        right after login/signup. If the user already has their own open
        cart, the guest's items are merged into it (quantities summed for
        matching products) and the guest order is discarded; otherwise the
        guest order is simply re-keyed onto the real user id.
        """
        guest_user_id = f"guest_{guest_id}"
        guest_cart = self._db.get_open_cart(guest_user_id)
        if guest_cart is None:
            return self.get_cart(authenticated_user_id)

        guest_items = self._db.get_order_with_children(guest_cart["order_id"])["items"]
        own_cart = self._db.get_open_cart(authenticated_user_id)

        if own_cart is None:
            self._db.reassign_cart_owner(guest_cart["order_id"], authenticated_user_id)
            return self.get_cart(authenticated_user_id)

        for row in guest_items:
            existing = self._db.get_cart_item(own_cart["order_id"], row["product_id"])
            new_quantity = int(row["quantity"]) + (int(existing["quantity"]) if existing else 0)
            product = {
                "product_id": row["product_id"],
                "name": row["name_snapshot"],
                "price": row["unit_price"],
            }
            self._put_cart_item(own_cart["order_id"], product, new_quantity)

        self._db.touch_cart_order(own_cart["order_id"])
        guest_item_sks = [row["sk"] for row in guest_items]
        self._db.delete_order_with_items(guest_cart["order_id"], guest_item_sks)

        return self.get_cart(authenticated_user_id)

    # ------------------------------------------------------------------
    # Cart — helpers
    # ------------------------------------------------------------------

    def _get_active_product(self, product_id: str) -> dict:
        product_map = self._db.batch_get_products([product_id])
        product = product_map.get(product_id)
        if not product:
            raise ValidationError(f"Product {product_id} not found")
        if not product.get("active"):
            raise ValidationError(f"Product {product_id} is not available")
        return product

    def _create_cart(self, user_id: str) -> dict:
        order_id = str(uuid.uuid4())
        created_at = _now_iso()
        order_item = {
            "order_id": order_id,
            "sk": "ORDER",
            "user_id": user_id,
            "status": "cart",
            "created_at": created_at,
            "updated_at": created_at,
        }
        self._db.create_cart_order(order_item)
        return order_item

    def _put_cart_item(self, order_id: str, product: dict, quantity: int) -> None:
        unit_price = Decimal(str(product["price"]))
        self._db.put_cart_item({
            "order_id": order_id,
            "sk": f"ITEM#{product['product_id']}",
            "product_id": str(product["product_id"]),
            "quantity": quantity,
            "unit_price": str(unit_price),
            "line_total": str(_cents(unit_price * quantity)),
            "name_snapshot": product["name"],
        })

    @staticmethod
    def _empty_cart_response() -> dict:
        return {"order_id": None, "status": "cart", "items": [], "subtotal": "0.00"}

    @staticmethod
    def _cart_to_response(order: dict, item_rows: list[dict]) -> dict:
        items = [
            {
                "product_id": row["product_id"],
                "quantity": int(row["quantity"]),
                "unit_price": str(row["unit_price"]),
                "line_total": str(row["line_total"]),
                "name_snapshot": row["name_snapshot"],
            }
            for row in item_rows
        ]
        subtotal = _cents(sum((Decimal(str(i["line_total"])) for i in items), Decimal("0")))
        return {
            "order_id": order["order_id"],
            "status": order["status"],
            "items": items,
            "subtotal": str(subtotal),
        }

    # ------------------------------------------------------------------
    # Product resolution
    # ------------------------------------------------------------------

    def _resolve_items(self, items) -> list[ResolvedOrderItem]:
        product_ids = [str(i.product_id) for i in items]
        product_map = self._db.batch_get_products(product_ids)

        resolved = []
        for item in items:
            product = product_map.get(str(item.product_id))
            if not product:
                raise ValidationError(f"Product {item.product_id} not found")
            if not product.get("active"):
                raise ValidationError(f"Product {item.product_id} is not available")
            qty = int(product["qty"])
            if qty < item.quantity:
                raise ValidationError(
                    f"Insufficient stock for '{product['name']}' "
                    f"(requested {item.quantity}, available {qty})"
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
    ) -> tuple[str | None, Decimal]:
        """
        Returns (resolved_code, discount_amount). The promotions table's
        natural key IS the code (no separate surrogate id in the new
        schema), so what used to be a numeric promotion_id is now just the
        normalized code string itself.
        """
        if not code:
            return None, Decimal("0")

        normalized_code = code.strip().upper()
        promo = self._db.get_promotion(normalized_code)

        if not promo:
            raise ValidationError(f"Promotion code '{code}' is not valid")

        if not promo.get("active"):
            raise ValidationError(f"Promotion code '{code}' is no longer active")

        subtotal = sum(i.line_total for i in items)
        value    = Decimal(str(promo["discount_value"]))

        if promo["discount_type"] == "percentage":
            discount = _cents(subtotal * (value / 100))
        else:
            discount = _cents(min(value, subtotal))

        return normalized_code, discount

    # ------------------------------------------------------------------
    # DB writes
    # ------------------------------------------------------------------

    @staticmethod
    def _make_item_child(order_id: str, idx: int, item: ResolvedOrderItem) -> dict:
        return {
            "order_id": order_id,
            "sk": f"ITEM#{idx:04d}",
            "product_id": str(item.product_id),
            "quantity": item.quantity,
            "unit_price": str(item.unit_price),
            "line_total": str(item.line_total),
            "name_snapshot": item.name_snapshot,
        }

    def _insert_order(self, o: ResolvedOrder) -> str:
        order_id = str(uuid.uuid4())
        created_at = _now_iso()

        order_item = {
            "order_id": order_id,
            "sk": "ORDER",
            "user_id": str(o.user_id),
            "status": "pending",
            "subtotal": str(o.subtotal),
            "tax_amount": str(o.tax_amount),
            "shipping_amount": str(o.shipping_amount),
            "total_amount": str(o.total_amount),
            "customer_notes": o.customer_notes,
            "connection_id": o.connection_id,
            "shipping_name": o.shipping.name,
            "shipping_address1": o.shipping.address1,
            "shipping_address2": o.shipping.address2,
            "shipping_city": o.shipping.city,
            "shipping_province": o.shipping.province,
            "shipping_postal_code": o.shipping.postal_code,
            "shipping_country": o.shipping.country,
            "created_at": created_at,
            "updated_at": created_at,
        }
        if o.promotion_id:  # holds the normalized promo code (see _resolve_promotion)
            order_item["applied_promotions"] = [
                {"code": o.promotion_id, "discount_amount": str(o.discount_amount)}
            ]
        # Drop unset optional fields — avoid storing None values as item attributes.
        order_item = {k: v for k, v in order_item.items() if v is not None}

        item_children = [
            self._make_item_child(order_id, idx, item)
            for idx, item in enumerate(o.items)
        ]

        stock_decrements = [
            {"product_id": str(item.product_id), "quantity": item.quantity}
            for item in o.items
        ]

        try:
            self._db.create_order_transaction(order_item, item_children, stock_decrements)
        except self._InsufficientStock as e:
            logger.error("Stock race on order create: %s", e)
            raise ValidationError(
                "One or more items sold out while your order was being placed. Please try again."
            )

        for item in o.items:
            logger.info(
                "Order item persisted | order_id=%s product_id=%s qty=%s",
                order_id, item.product_id, item.quantity,
            )

        return order_id

    # ------------------------------------------------------------------
    # EventBridge
    # ------------------------------------------------------------------

    def _emit_order_created(self, order_id: str, o: ResolvedOrder) -> None:
        """
        Fires an OrderCreated event. The Payment Lambda listens on this bus
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

    def _emit_low_stock_if_needed(self, items: list) -> None:
        """
        After an order is saved, re-read each ordered product's now-current
        stock. For any that dropped to/below their low-stock threshold,
        emit LowStockDetected AND flip on the products table's sparse
        reorder_flag (see db.py) so the low-stock report picks it up. For
        any that are now comfortably restocked, clear the flag.
        """
        product_ids = [str(i.product_id) for i in items]
        product_map = self._db.batch_get_products(product_ids)

        low = []
        for pid in product_ids:
            product = product_map.get(pid)
            if not product:
                continue
            qty = int(product["qty"])
            threshold = int(product["low_stock_threshold"])

            try:
                self._db.update_product_reorder_state(pid, qty, threshold)
            except ClientError as e:
                logger.error("Failed to update reorder_flag for product %s: %s", pid, e)

            if qty <= threshold:
                low.append(product)

        if not low:
            return

        logger.info("Low stock detected for %d product(s)", len(low))
        detail = {"products": [
            {
                "product_id":    p["product_id"],
                "sku":           p.get("sku"),
                "name":          p.get("name"),
                "category":      p.get("category"),
                "current_stock": int(p["qty"]),
                "threshold":     int(p["low_stock_threshold"]),
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

    # ------------------------------------------------------------------
    # Response shaping
    # ------------------------------------------------------------------

    @staticmethod
    def _order_to_response(order: dict, item_rows: list[dict]) -> dict:
        return {
            "order_id": order["order_id"],
            "user_id": order["user_id"],
            "status": order["status"],
            "subtotal": str(order["subtotal"]),
            "tax": str(order["tax_amount"]),
            "shipping_fee": str(order["shipping_amount"]),
            "total": str(order["total_amount"]),
            "customer_notes": order.get("customer_notes"),
            "created_at": order["created_at"],
            "shipping_address": {
                "name": order["shipping_name"],
                "address1": order["shipping_address1"],
                "address2": order.get("shipping_address2"),
                "city": order["shipping_city"],
                "province": order["shipping_province"],
                "postal_code": order["shipping_postal_code"],
                "country": order["shipping_country"],
            },
            "items": [
                {
                    "product_id": row["product_id"],
                    "quantity": int(row["quantity"]),
                    "unit_price": str(row["unit_price"]),
                    "line_total": str(row["line_total"]),
                    "name_snapshot": row["name_snapshot"],
                }
                for row in item_rows
            ],
        }
