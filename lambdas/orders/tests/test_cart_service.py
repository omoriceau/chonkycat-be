from decimal import Decimal

import pytest

from models import CheckoutCartRequest, ShippingAddress, ValidationError


def _shipping():
    return ShippingAddress(
        name="Benny Garcia", address1="42 Maple Ave", city="Toronto",
        province="ON", postal_code="M5V 2H1", country="Canada",
    )


class TestAddCartItem:
    def test_first_add_creates_a_cart(self, service, products_table, make_product):
        make_product(products_table, product_id="p1", price="10.00")

        cart = service.add_cart_item("guest_abc", "p1", 2)

        assert cart["order_id"]
        assert cart["status"] == "cart"
        assert len(cart["items"]) == 1
        assert cart["items"][0]["product_id"] == "p1"
        assert cart["items"][0]["quantity"] == 2
        assert cart["items"][0]["line_total"] == "20.00"
        assert cart["subtotal"] == "20.00"

    def test_repeat_add_increments_quantity_on_same_order(self, service, products_table, make_product):
        make_product(products_table, product_id="p1", price="10.00")

        first = service.add_cart_item("guest_abc", "p1", 2)
        second = service.add_cart_item("guest_abc", "p1", 3)

        assert second["order_id"] == first["order_id"]
        assert len(second["items"]) == 1
        assert second["items"][0]["quantity"] == 5
        assert second["subtotal"] == "50.00"

    def test_adding_a_different_product_adds_a_second_line(self, service, products_table, make_product):
        make_product(products_table, product_id="p1", price="10.00")
        make_product(products_table, product_id="p2", price="5.00")

        service.add_cart_item("guest_abc", "p1", 1)
        cart = service.add_cart_item("guest_abc", "p2", 4)

        assert len(cart["items"]) == 2
        assert cart["subtotal"] == "30.00"  # 10 + (5*4)

    def test_add_unknown_product_rejected(self, service, products_table):
        with pytest.raises(ValidationError):
            service.add_cart_item("guest_abc", "does-not-exist", 1)

    def test_add_inactive_product_rejected(self, service, products_table, make_product):
        make_product(products_table, product_id="p1", price="10.00", active=False)
        with pytest.raises(ValidationError):
            service.add_cart_item("guest_abc", "p1", 1)


class TestUpdateAndRemoveCartItem:
    def test_update_sets_absolute_quantity(self, service, products_table, make_product):
        make_product(products_table, product_id="p1", price="10.00")
        service.add_cart_item("guest_abc", "p1", 2)

        cart = service.update_cart_item("guest_abc", "p1", 7)

        assert cart["items"][0]["quantity"] == 7
        assert cart["subtotal"] == "70.00"

    def test_update_to_zero_removes_the_line(self, service, products_table, make_product):
        make_product(products_table, product_id="p1", price="10.00")
        service.add_cart_item("guest_abc", "p1", 2)

        cart = service.update_cart_item("guest_abc", "p1", 0)

        assert cart["items"] == []
        assert cart["subtotal"] == "0.00"

    def test_remove_cart_item(self, service, products_table, make_product):
        make_product(products_table, product_id="p1", price="10.00")
        make_product(products_table, product_id="p2", price="5.00")
        service.add_cart_item("guest_abc", "p1", 1)
        service.add_cart_item("guest_abc", "p2", 1)

        cart = service.remove_cart_item("guest_abc", "p1")

        assert len(cart["items"]) == 1
        assert cart["items"][0]["product_id"] == "p2"

    def test_update_with_no_cart_raises(self, service):
        with pytest.raises(ValidationError):
            service.update_cart_item("guest_nobody", "p1", 1)


class TestGetCart:
    def test_empty_cart_shape(self, service):
        cart = service.get_cart("guest_new")
        assert cart == {"order_id": None, "status": "cart", "items": [], "subtotal": "0.00"}


class TestCheckoutCart:
    def _build_cart(self, service, products_table, make_product, user_id="guest_abc"):
        make_product(products_table, product_id="p1", price="10.00", qty=5)
        cart = service.add_cart_item(user_id, "p1", 2)
        return cart["order_id"]

    def test_checkout_transitions_status_and_decrements_stock(
        self, service, products_table, make_product, events_client
    ):
        order_id = self._build_cart(service, products_table, make_product)

        result = service.checkout_cart("guest_abc", order_id, CheckoutCartRequest(
            shipping=_shipping(), customer_email="benny@example.com",
        ))

        assert result["order_id"] == order_id
        assert result["status"] == "pending"
        assert result["subtotal"] == "20.00"

        remaining = products_table.get_item(Key={"product_id": "p1"})["Item"]
        assert int(remaining["qty"]) == 3  # 5 - 2

        detail_types = [
            call.kwargs["Entries"][0]["DetailType"]
            for call in events_client.put_events.call_args_list
        ]
        assert "OrderCreated" in detail_types

        # Cart is gone from the open-cart index — it's a real order now.
        assert service.get_cart("guest_abc") == {
            "order_id": None, "status": "cart", "items": [], "subtotal": "0.00"
        }

    def test_checkout_insufficient_stock_rejected(self, service, products_table, make_product):
        make_product(products_table, product_id="p1", price="10.00", qty=1)
        cart = service.add_cart_item("guest_abc", "p1", 5)  # more than the 1 in stock

        with pytest.raises(ValidationError):
            service.checkout_cart("guest_abc", cart["order_id"], CheckoutCartRequest(
                shipping=_shipping(), customer_email="benny@example.com",
            ))

    def test_checkout_rejects_someone_elses_cart(self, service, products_table, make_product):
        order_id = self._build_cart(service, products_table, make_product, user_id="guest_owner")

        with pytest.raises(ValidationError):
            service.checkout_cart("guest_intruder", order_id, CheckoutCartRequest(
                shipping=_shipping(), customer_email="intruder@example.com",
            ))

    def test_checkout_rejects_already_checked_out_order(self, service, products_table, make_product):
        order_id = self._build_cart(service, products_table, make_product)
        service.checkout_cart("guest_abc", order_id, CheckoutCartRequest(
            shipping=_shipping(), customer_email="benny@example.com",
        ))

        with pytest.raises(ValidationError):
            service.checkout_cart("guest_abc", order_id, CheckoutCartRequest(
                shipping=_shipping(), customer_email="benny@example.com",
            ))

    def test_checkout_empty_cart_rejected(self, service):
        with pytest.raises(ValidationError):
            service.checkout_cart("guest_abc", "nonexistent-order-id", CheckoutCartRequest(
                shipping=_shipping(), customer_email="benny@example.com",
            ))


class TestClaimGuestCart:
    def test_claim_with_no_existing_user_cart_reassigns_in_place(self, service, products_table, make_product):
        make_product(products_table, product_id="p1", price="10.00")
        guest_cart = service.add_cart_item("guest_abc123", "p1", 2)

        claimed = service.claim_guest_cart("cognito-sub-1", "abc123")

        assert claimed["order_id"] == guest_cart["order_id"]
        assert claimed["items"][0]["quantity"] == 2
        # The guest identity no longer owns any open cart.
        assert service.get_cart("guest_abc123")["order_id"] is None

    def test_claim_merges_into_existing_user_cart(self, service, products_table, make_product):
        make_product(products_table, product_id="p1", price="10.00")
        make_product(products_table, product_id="p2", price="5.00")

        user_cart = service.add_cart_item("cognito-sub-1", "p1", 1)
        guest_cart = service.add_cart_item("guest_xyz", "p1", 2)
        service.add_cart_item("guest_xyz", "p2", 3)

        claimed = service.claim_guest_cart("cognito-sub-1", "xyz")

        # Same order_id as the user's pre-existing cart — not the guest's.
        assert claimed["order_id"] == user_cart["order_id"]
        by_product = {i["product_id"]: i["quantity"] for i in claimed["items"]}
        assert by_product == {"p1": 3, "p2": 3}  # p1: 1 (user) + 2 (guest)

        # The guest's cart order is gone entirely.
        assert service.get_cart("guest_xyz")["order_id"] is None

    def test_claim_with_no_guest_cart_is_a_no_op(self, service, products_table, make_product):
        make_product(products_table, product_id="p1", price="10.00")
        user_cart = service.add_cart_item("cognito-sub-1", "p1", 1)

        claimed = service.claim_guest_cart("cognito-sub-1", "never-had-a-cart")

        assert claimed["order_id"] == user_cart["order_id"]
        assert claimed["items"][0]["quantity"] == 1
