"""
Direct OrderService unit tests covering order creation, updates, promotion
resolution, and low-stock emission — the parts of service.py not already
exercised by test_cart_service.py (cart mechanics) or test_list_orders.py
(admin listing).
"""
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from models import (
    CheckoutCartRequest,
    CreateOrderRequest,
    OrderItemRequest,
    ShippingAddress,
    ValidationError,
)


def _shipping(**overrides):
    d = dict(
        name="Benny Garcia", address1="42 Maple Ave", city="Toronto",
        province="ON", postal_code="M5V 2H1", country="Canada",
    )
    d.update(overrides)
    return ShippingAddress(**d)


def _create_request(**overrides):
    kwargs = dict(
        user_id="u1",
        items=[OrderItemRequest(product_id="p1", quantity=2)],
        shipping=_shipping(),
        customer_email="benny@example.com",
    )
    kwargs.update(overrides)
    return CreateOrderRequest(**kwargs)


class TestGetOrder:
    def test_returns_none_when_not_found(self, service):
        assert service.get_order("nonexistent") is None

    def test_returns_none_for_soft_deleted_order(self, service, orders_table):
        orders_table.put_item(Item={
            "order_id": "o1", "sk": "ORDER", "user_id": "u1", "status": "pending",
            "subtotal": "10.00", "tax_amount": "1.30", "shipping_amount": "10.00",
            "total_amount": "21.30", "created_at": "2026-01-01T00:00:00.000000Z",
            "shipping_name": "Benny", "shipping_address1": "1 Main", "shipping_city": "Toronto",
            "shipping_province": "ON", "shipping_postal_code": "M5V 2H1", "shipping_country": "Canada",
            "deleted_at": "2026-02-01T00:00:00.000000Z",
        })
        assert service.get_order("o1") is None

    def test_returns_full_order_detail(self, service, products_table, make_product):
        make_product(products_table, product_id="p1", price="10.00", qty=5)
        order = service.create_order(_create_request())
        result = service.get_order(order["order_id"])
        assert result["order_id"] == order["order_id"]
        assert result["items"][0]["product_id"] == "p1"
        assert result["shipping_address"]["city"] == "Toronto"


class TestDeleteOrder:
    def test_delete_reraises_unexpected_exceptions(self, monkeypatch):
        import service as service_module
        mock_db = MagicMock()
        mock_db.soft_delete_order.side_effect = RuntimeError("db exploded")
        svc = service_module.OrderService(db_client=mock_db, events_client=MagicMock())

        with pytest.raises(RuntimeError, match="db exploded"):
            svc.delete_order("o1")


class TestUpdateOrder:
    def _create_pending_order(self, service, products_table, make_product):
        make_product(products_table, product_id="p1", price="10.00", qty=5)
        return service.create_order(_create_request())["order_id"]

    def test_returns_none_when_order_not_found(self, service):
        assert service.update_order("nonexistent", {"customer_notes": "hi"}) is None

    def test_rejects_update_on_non_pending_order(self, service, orders_table):
        orders_table.put_item(Item={
            "order_id": "o1", "sk": "ORDER", "user_id": "u1", "status": "completed",
            "subtotal": "10.00", "tax_amount": "1.30", "shipping_amount": "10.00",
            "total_amount": "21.30", "created_at": "2026-01-01T00:00:00.000000Z",
            "shipping_name": "Benny", "shipping_address1": "1 Main", "shipping_city": "Toronto",
            "shipping_province": "ON", "shipping_postal_code": "M5V 2H1", "shipping_country": "Canada",
        })
        with pytest.raises(ValidationError, match="completed"):
            service.update_order("o1", {"customer_notes": "hi"})

    def test_update_customer_notes_only_keeps_existing_items(self, service, products_table, make_product):
        order_id = self._create_pending_order(service, products_table, make_product)

        result = service.update_order(order_id, {"customer_notes": "Ring the bell"})

        assert result["order_id"] == order_id
        assert result["items"][0]["product_id"] == "p1"
        assert result["subtotal"] == "20.00"
        stored = service.get_order(order_id)
        assert stored["customer_notes"] == "Ring the bell"

    def test_update_items_replaces_line_items_and_recalculates_totals(
        self, service, products_table, make_product
    ):
        order_id = self._create_pending_order(service, products_table, make_product)
        make_product(products_table, product_id="p2", price="5.00", qty=10)

        result = service.update_order(order_id, {
            "items": [OrderItemRequest(product_id="p2", quantity=3)]
        })

        assert result["items"] == [{
            "product_id": "p2", "name": "Chonky Salmon", "quantity": 3,
            "unit_price": "5.00", "line_total": "15.00",
        }]
        assert result["subtotal"] == "15.00"

        stored = service.get_order(order_id)
        assert len(stored["items"]) == 1
        assert stored["items"][0]["product_id"] == "p2"

    def test_update_shipping_address(self, service, products_table, make_product):
        order_id = self._create_pending_order(service, products_table, make_product)

        result = service.update_order(order_id, {
            "shipping": _shipping(city="Ottawa", name="New Recipient")
        })

        assert result["order_id"] == order_id
        stored = service.get_order(order_id)
        assert stored["shipping_address"]["city"] == "Ottawa"
        assert stored["shipping_address"]["name"] == "New Recipient"

    def test_update_customer_notes_explicit_none_is_treated_as_unchanged(
        self, service, products_table, make_product
    ):
        # service.update_order() can't distinguish "explicitly cleared" from
        # "not provided" — `customer_notes if customer_notes is not None else
        # current.get(...)` falls back to the existing value either way — so
        # sending customer_notes=None leaves the prior note in place.
        order_id = self._create_pending_order(service, products_table, make_product)
        service.update_order(order_id, {"customer_notes": "temp note"})

        service.update_order(order_id, {"customer_notes": None})

        stored = service.get_order(order_id)
        assert stored["customer_notes"] == "temp note"

    def test_update_with_promotion_code_applies_discount(
        self, service, products_table, make_product, promotions_table, make_promotion
    ):
        order_id = self._create_pending_order(service, products_table, make_product)
        make_promotion(promotions_table, code="SAVE10", discount_type="fixed", discount_value="5.00")

        result = service.update_order(order_id, {"promotion_code": "save10"})

        assert result["discount"] == "5.00"
        # subtotal 20.00 - 5.00 discount = 15.00 (still under free-ship threshold)
        assert Decimal(result["total"]) == Decimal(result["subtotal"]) - Decimal("5.00") + Decimal(result["tax"]) + Decimal(result["shipping"])

    def test_update_with_invalid_promotion_code_raises(self, service, products_table, make_product):
        order_id = self._create_pending_order(service, products_table, make_product)

        with pytest.raises(ValidationError, match="not valid"):
            service.update_order(order_id, {"promotion_code": "NOPE"})

    def test_update_items_with_unknown_product_raises(self, service, products_table, make_product):
        order_id = self._create_pending_order(service, products_table, make_product)

        with pytest.raises(ValidationError, match="not found"):
            service.update_order(order_id, {
                "items": [OrderItemRequest(product_id="ghost", quantity=1)]
            })


class TestResolveItems:
    def test_unknown_product_raises(self, service, products_table):
        with pytest.raises(ValidationError, match="not found"):
            service.create_order(_create_request(
                items=[OrderItemRequest(product_id="ghost", quantity=1)]
            ))

    def test_inactive_product_raises(self, service, products_table, make_product):
        make_product(products_table, product_id="p1", price="10.00", qty=5, active=False)
        with pytest.raises(ValidationError, match="not available"):
            service.create_order(_create_request())

    def test_insufficient_stock_raises(self, service, products_table, make_product):
        make_product(products_table, product_id="p1", price="10.00", qty=1)
        with pytest.raises(ValidationError, match="Insufficient stock"):
            service.create_order(_create_request(
                items=[OrderItemRequest(product_id="p1", quantity=5)]
            ))


class TestResolvePromotion:
    def test_no_code_returns_zero_discount(self, service):
        code, discount = service._resolve_promotion(None, [])
        assert code is None
        assert discount == Decimal("0")

    def test_blank_code_returns_zero_discount(self, service):
        code, discount = service._resolve_promotion("", [])
        assert code is None
        assert discount == Decimal("0")

    def test_unknown_code_raises(self, service, promotions_table):
        with pytest.raises(ValidationError, match="not valid"):
            service._resolve_promotion("NOPE", [])

    def test_inactive_code_raises(self, service, promotions_table, make_promotion):
        make_promotion(promotions_table, code="OLD5", active=False)
        with pytest.raises(ValidationError, match="no longer active"):
            service._resolve_promotion("OLD5", [])

    def test_percentage_discount(self, service, promotions_table, make_promotion):
        from models import ResolvedOrderItem
        make_promotion(promotions_table, code="TEN", discount_type="percentage", discount_value="10")
        items = [ResolvedOrderItem(
            product_id="p1", name_snapshot="Widget", quantity=1,
            unit_price=Decimal("100.00"), line_total=Decimal("100.00"),
        )]
        code, discount = service._resolve_promotion("ten", items)
        assert code == "TEN"
        assert discount == Decimal("10.00")

    def test_fixed_discount_capped_at_subtotal(self, service, promotions_table, make_promotion):
        from models import ResolvedOrderItem
        make_promotion(promotions_table, code="BIG50", discount_type="fixed", discount_value="50.00")
        items = [ResolvedOrderItem(
            product_id="p1", name_snapshot="Widget", quantity=1,
            unit_price=Decimal("10.00"), line_total=Decimal("10.00"),
        )]
        code, discount = service._resolve_promotion("BIG50", items)
        assert discount == Decimal("10.00")  # capped, not 50.00


class TestCreateOrderFullFlow:
    def test_create_order_with_promotion_and_free_shipping(
        self, service, products_table, make_product, promotions_table, make_promotion, events_client
    ):
        make_product(products_table, product_id="p1", price="50.00", qty=5)
        make_promotion(promotions_table, code="WELCOME10", discount_type="percentage", discount_value="10")

        result = service.create_order(_create_request(
            items=[OrderItemRequest(product_id="p1", quantity=2)],  # 100.00 subtotal
            promotion_code="welcome10",
        ))

        assert result["subtotal"] == "100.00"
        assert result["discount"] == "10.00"
        # discounted_sub (90) >= FREE_SHIP_ABOVE (75) -> Decimal("0"), which
        # str()s as "0" (not "0.00" — it's never passed through _cents()).
        assert result["shipping"] == "0"
        assert Decimal(result["total"]) > Decimal("0")

        detail_types = [
            call.kwargs["Entries"][0]["DetailType"]
            for call in events_client.put_events.call_args_list
        ]
        assert "OrderCreated" in detail_types

    def test_create_order_below_free_ship_threshold_charges_flat_fee(
        self, service, products_table, make_product
    ):
        make_product(products_table, product_id="p1", price="10.00", qty=5)
        result = service.create_order(_create_request(
            items=[OrderItemRequest(product_id="p1", quantity=1)]
        ))
        assert result["shipping"] == "10.00"

    def test_insufficient_stock_race_maps_to_validation_error(self, monkeypatch):
        import service as service_module
        import db as db_module

        mock_db = MagicMock()
        mock_db.batch_get_products.return_value = {
            "p1": {"product_id": "p1", "name": "Widget", "price": "10.00", "qty": 5, "active": True, "low_stock_threshold": 1}
        }
        mock_db.create_order_transaction.side_effect = db_module.InsufficientStock("p1")
        svc = service_module.OrderService(db_client=mock_db, events_client=MagicMock())

        with pytest.raises(ValidationError, match="sold out"):
            svc.create_order(_create_request())


class TestRemoveCartItemNoCart:
    def test_raises_when_no_open_cart(self, service):
        with pytest.raises(ValidationError, match="Cart is empty"):
            service.remove_cart_item("guest_nobody", "p1")


class TestCheckoutCartEdgeCases:
    def test_checkout_empty_item_cart_rejected(self, service, orders_table):
        # A cart ORDER row with zero ITEM# children (bypasses add_cart_item).
        orders_table.put_item(Item={
            "order_id": "empty-cart", "sk": "ORDER", "user_id": "guest_x",
            "status": "cart", "created_at": "2026-01-01T00:00:00.000000Z",
            "updated_at": "2026-01-01T00:00:00.000000Z",
        })
        with pytest.raises(ValidationError, match="Cart is empty"):
            service.checkout_cart("guest_x", "empty-cart", CheckoutCartRequest(
                shipping=_shipping(), customer_email="x@example.com",
            ))

    def test_checkout_with_promotion_code(
        self, service, products_table, make_product, promotions_table, make_promotion
    ):
        make_product(products_table, product_id="p1", price="10.00", qty=5)
        make_promotion(promotions_table, code="SAVE5", discount_type="fixed", discount_value="5.00")
        cart = service.add_cart_item("guest_promo", "p1", 2)

        result = service.checkout_cart("guest_promo", cart["order_id"], CheckoutCartRequest(
            shipping=_shipping(), customer_email="x@example.com", promotion_code="save5",
        ))
        assert result["discount"] == "5.00"

    def test_checkout_conditional_check_failed_maps_to_validation_error(self):
        import service as service_module
        mock_db = MagicMock()
        mock_db.get_order_with_children.return_value = {
            "order": {"user_id": "u1", "status": "cart"},
            "items": [{"product_id": "p1", "quantity": 1}],
        }
        mock_db.batch_get_products.return_value = {
            "p1": {"product_id": "p1", "name": "Widget", "price": "10.00", "qty": 5, "active": True, "low_stock_threshold": 1}
        }
        mock_db.finalize_cart_order.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "UpdateItem"
        )
        svc = service_module.OrderService(db_client=mock_db, events_client=MagicMock())

        with pytest.raises(ValidationError, match="already been checked out"):
            svc.checkout_cart("u1", "o1", CheckoutCartRequest(
                shipping=_shipping(), customer_email="x@example.com",
            ))

    def test_checkout_other_client_error_propagates(self):
        import service as service_module
        mock_db = MagicMock()
        mock_db.get_order_with_children.return_value = {
            "order": {"user_id": "u1", "status": "cart"},
            "items": [{"product_id": "p1", "quantity": 1}],
        }
        mock_db.batch_get_products.return_value = {
            "p1": {"product_id": "p1", "name": "Widget", "price": "10.00", "qty": 5, "active": True, "low_stock_threshold": 1}
        }
        mock_db.finalize_cart_order.side_effect = ClientError(
            {"Error": {"Code": "SomeOtherError", "Message": "x"}}, "UpdateItem"
        )
        svc = service_module.OrderService(db_client=mock_db, events_client=MagicMock())

        with pytest.raises(ClientError):
            svc.checkout_cart("u1", "o1", CheckoutCartRequest(
                shipping=_shipping(), customer_email="x@example.com",
            ))


class TestEmitLowStockIfNeeded:
    def test_low_stock_sets_reorder_flag_and_emits_event(
        self, service, products_table, make_product, events_client
    ):
        make_product(products_table, product_id="p1", price="10.00", qty=3, low_stock_threshold=5)
        service._emit_low_stock_if_needed([
            _resolved_item("p1", 1),
        ])
        product = products_table.get_item(Key={"product_id": "p1"})["Item"]
        assert product.get("reorder_flag") == "true"

        detail_types = [
            call.kwargs["Entries"][0]["DetailType"]
            for call in events_client.put_events.call_args_list
        ]
        assert "LowStockDetected" in detail_types

    def test_healthy_stock_clears_reorder_flag_and_emits_nothing(
        self, service, products_table, make_product, events_client
    ):
        make_product(products_table, product_id="p1", price="10.00", qty=100, low_stock_threshold=5)
        service._emit_low_stock_if_needed([
            _resolved_item("p1", 1),
        ])
        product = products_table.get_item(Key={"product_id": "p1"})["Item"]
        assert "reorder_flag" not in product
        assert events_client.put_events.call_count == 0

    def test_missing_product_is_skipped(self, service, events_client):
        # product_id not in the products table at all -> continue branch
        service._emit_low_stock_if_needed([_resolved_item("ghost", 1)])
        assert events_client.put_events.call_count == 0

    def test_reorder_state_update_error_is_logged_and_does_not_raise(self):
        import service as service_module
        mock_db = MagicMock()
        mock_db.batch_get_products.return_value = {
            "p1": {"product_id": "p1", "name": "Widget", "qty": 1, "low_stock_threshold": 5, "sku": "SKU1", "category": "cat"}
        }
        mock_db.update_product_reorder_state.side_effect = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "x"}}, "UpdateItem"
        )
        svc = service_module.OrderService(db_client=mock_db, events_client=MagicMock())

        # Should not raise despite update_product_reorder_state failing.
        svc._emit_low_stock_if_needed([_resolved_item("p1", 1)])
        assert svc._events.put_events.called


def _resolved_item(product_id, quantity):
    from models import ResolvedOrderItem
    return ResolvedOrderItem(
        product_id=product_id, name_snapshot="Widget", quantity=quantity,
        unit_price=Decimal("10.00"), line_total=Decimal("10.00"),
    )
