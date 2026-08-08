"""
Direct unit tests for models.py's request parsers and validation helpers.
"""
import pytest

from models import (
    ValidationError,
    _require,
    _parse_shipping,
    parse_add_cart_item_request,
    parse_checkout_cart_request,
    parse_claim_cart_request,
    parse_create_order_request,
    parse_update_cart_item_request,
    parse_update_order_request,
)


def _shipping_dict(**overrides):
    d = {
        "name": "Benny Garcia", "address1": "42 Maple Ave", "city": "Toronto",
        "province": "ON", "postal_code": "M5V 2H1", "country": "Canada",
    }
    d.update(overrides)
    return d


class TestRequire:
    def test_missing_key_raises(self):
        with pytest.raises(ValidationError, match="Missing required field: 'foo'"):
            _require({}, "foo")

    def test_none_value_raises(self):
        with pytest.raises(ValidationError, match="foo"):
            _require({"foo": None}, "foo")

    def test_present_value_returned(self):
        assert _require({"foo": "bar"}, "foo") == "bar"

    def test_falsy_but_not_none_value_returned(self):
        # 0 / "" / False are valid values — only None is treated as missing.
        assert _require({"foo": 0}, "foo") == 0
        assert _require({"foo": ""}, "foo") == ""
        assert _require({"foo": False}, "foo") is False


class TestParseShipping:
    def test_full_shipping_parses(self):
        addr = _parse_shipping(_shipping_dict(address2="Unit 4"))
        assert addr.name == "Benny Garcia"
        assert addr.address2 == "Unit 4"

    def test_missing_required_field_raises(self):
        d = _shipping_dict()
        del d["city"]
        with pytest.raises(ValidationError, match="city"):
            _parse_shipping(d)

    def test_address2_defaults_to_none(self):
        addr = _parse_shipping(_shipping_dict())
        assert addr.address2 is None


class TestParseCreateOrderRequest:
    def _body(self, **overrides):
        body = {
            "user_id": "u1", "customer_email": "benny@example.com",
            "items": [{"product_id": "p1", "quantity": 2}],
            "shipping": _shipping_dict(),
        }
        body.update(overrides)
        return body

    def test_happy_path_defaults(self):
        req = parse_create_order_request(self._body())
        assert req.currency == "CAD"
        assert req.payment_provider == "stripe"
        assert req.promotion_code is None
        assert req.customer_notes is None
        assert req.connection_id is None
        assert len(req.items) == 1
        assert req.items[0].product_id == "p1"
        assert req.items[0].quantity == 2

    def test_currency_is_uppercased(self):
        req = parse_create_order_request(self._body(currency="usd"))
        assert req.currency == "USD"

    def test_items_not_a_list_raises(self):
        with pytest.raises(ValidationError, match="non-empty list"):
            parse_create_order_request(self._body(items="not-a-list"))

    def test_items_empty_list_raises(self):
        with pytest.raises(ValidationError, match="non-empty list"):
            parse_create_order_request(self._body(items=[]))

    def test_item_missing_product_id_raises(self):
        with pytest.raises(ValidationError, match="product_id"):
            parse_create_order_request(self._body(items=[{"quantity": 1}]))

    def test_item_non_numeric_quantity_raises(self):
        with pytest.raises(ValidationError, match=r"items\[0\]"):
            parse_create_order_request(self._body(items=[{"product_id": "p1", "quantity": "abc"}]))

    def test_item_quantity_below_one_raises(self):
        with pytest.raises(ValidationError, match="quantity must be >= 1"):
            parse_create_order_request(self._body(items=[{"product_id": "p1", "quantity": 0}]))

    def test_missing_user_id_raises(self):
        body = self._body()
        del body["user_id"]
        with pytest.raises(ValidationError, match="user_id"):
            parse_create_order_request(body)

    def test_missing_shipping_raises(self):
        body = self._body()
        del body["shipping"]
        with pytest.raises(ValidationError, match="shipping"):
            parse_create_order_request(body)

    def test_optional_fields_passed_through(self):
        req = parse_create_order_request(self._body(
            promotion_code="WELCOME10", customer_notes="Leave at door",
            connection_id="conn-1", payment_provider="paypal",
        ))
        assert req.promotion_code == "WELCOME10"
        assert req.customer_notes == "Leave at door"
        assert req.connection_id == "conn-1"
        assert req.payment_provider == "paypal"


class TestParseUpdateOrderRequest:
    def test_no_fields_raises(self):
        with pytest.raises(ValidationError, match="At least one field"):
            parse_update_order_request({})

    def test_items_update(self):
        update = parse_update_order_request({"items": [{"product_id": "p1", "quantity": 3}]})
        assert update["items"][0].product_id == "p1"
        assert update["items"][0].quantity == 3

    def test_items_not_a_list_raises(self):
        with pytest.raises(ValidationError, match="non-empty list"):
            parse_update_order_request({"items": "nope"})

    def test_items_empty_list_raises(self):
        with pytest.raises(ValidationError, match="non-empty list"):
            parse_update_order_request({"items": []})

    def test_item_bad_quantity_type_raises(self):
        with pytest.raises(ValidationError, match=r"items\[0\]"):
            parse_update_order_request({"items": [{"product_id": "p1", "quantity": "x"}]})

    def test_item_quantity_below_one_raises(self):
        with pytest.raises(ValidationError, match="quantity must be >= 1"):
            parse_update_order_request({"items": [{"product_id": "p1", "quantity": 0}]})

    def test_shipping_update(self):
        update = parse_update_order_request({"shipping": _shipping_dict()})
        assert update["shipping"].city == "Toronto"

    def test_customer_notes_update(self):
        update = parse_update_order_request({"customer_notes": "hi"})
        assert update["customer_notes"] == "hi"

    def test_customer_notes_explicit_none_is_kept(self):
        update = parse_update_order_request({"customer_notes": None})
        assert "customer_notes" in update
        assert update["customer_notes"] is None

    def test_promotion_code_update(self):
        update = parse_update_order_request({"promotion_code": "SAVE10"})
        assert update["promotion_code"] == "SAVE10"

    def test_multiple_fields_combined(self):
        update = parse_update_order_request({
            "customer_notes": "hi", "promotion_code": "SAVE10",
        })
        assert set(update.keys()) == {"customer_notes", "promotion_code"}


class TestParseAddCartItemRequest:
    def test_happy_path(self):
        req = parse_add_cart_item_request({"product_id": "p1", "quantity": 2})
        assert req.product_id == "p1"
        assert req.quantity == 2

    def test_non_numeric_quantity_raises(self):
        with pytest.raises(ValidationError, match="must be an integer"):
            parse_add_cart_item_request({"product_id": "p1", "quantity": "abc"})

    def test_quantity_below_one_raises(self):
        with pytest.raises(ValidationError, match=">= 1"):
            parse_add_cart_item_request({"product_id": "p1", "quantity": 0})

    def test_missing_product_id_raises(self):
        with pytest.raises(ValidationError, match="product_id"):
            parse_add_cart_item_request({"quantity": 1})


class TestParseUpdateCartItemRequest:
    def test_happy_path(self):
        req = parse_update_cart_item_request({"quantity": 5})
        assert req.quantity == 5

    def test_zero_is_allowed(self):
        req = parse_update_cart_item_request({"quantity": 0})
        assert req.quantity == 0

    def test_non_numeric_quantity_raises(self):
        with pytest.raises(ValidationError, match="must be an integer"):
            parse_update_cart_item_request({"quantity": "abc"})

    def test_negative_quantity_raises(self):
        with pytest.raises(ValidationError, match=">= 0"):
            parse_update_cart_item_request({"quantity": -1})


class TestParseClaimCartRequest:
    def test_happy_path(self):
        req = parse_claim_cart_request({"guest_id": "abc-123"})
        assert req.guest_id == "abc-123"

    def test_missing_guest_id_raises(self):
        with pytest.raises(ValidationError, match="guest_id"):
            parse_claim_cart_request({})


class TestParseCheckoutCartRequest:
    def test_defaults(self):
        req = parse_checkout_cart_request({
            "customer_email": "x@example.com", "shipping": _shipping_dict(),
        })
        assert req.currency == "CAD"
        assert req.payment_provider == "stripe"
        assert req.promotion_code is None

    def test_currency_is_uppercased(self):
        req = parse_checkout_cart_request({
            "customer_email": "x@example.com", "shipping": _shipping_dict(), "currency": "usd",
        })
        assert req.currency == "USD"

    def test_missing_customer_email_raises(self):
        with pytest.raises(ValidationError, match="customer_email"):
            parse_checkout_cart_request({"shipping": _shipping_dict()})

    def test_optional_fields_passed_through(self):
        req = parse_checkout_cart_request({
            "customer_email": "x@example.com", "shipping": _shipping_dict(),
            "promotion_code": "SAVE10", "customer_notes": "hi", "connection_id": "conn-1",
        })
        assert req.promotion_code == "SAVE10"
        assert req.customer_notes == "hi"
        assert req.connection_id == "conn-1"
