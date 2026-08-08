"""
Covers lambda_handler.py routes not already exercised by test_cart_routes.py /
test_list_orders.py: preflight, unsupported methods, order create/update/delete,
JSON parsing errors, validation errors, and the "Internal server error" 500
branches (via monkeypatched service raising unexpected exceptions).
"""
import json
from unittest.mock import MagicMock

import pytest

from tests.conftest import body_of


def _shipping():
    return {
        "name": "Benny Garcia", "address1": "42 Maple Ave", "city": "Toronto",
        "province": "ON", "postal_code": "M5V 2H1", "country": "Canada",
    }


class TestPreflightAndRouting:
    def test_preflight_options_request(self, dynamodb_tables, make_event):
        import lambda_handler
        event = make_event("OPTIONS", resource="/orders")
        event["httpMethod"] = "OPTIONS"
        resp = lambda_handler.lambda_handler(event, None)
        assert resp["statusCode"] == 204

    def test_unsupported_method_on_orders_root(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event("PATCH", resource="/orders"), None
        )
        assert resp["statusCode"] == 405
        assert "Unsupported HTTP method" in body_of(resp)["error"]

    def test_unsupported_cart_route(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event("PATCH", resource="/cart/weird"), None
        )
        assert resp["statusCode"] == 405
        assert "Unsupported route" in body_of(resp)["error"]


class TestListMyOrders:
    def test_requires_identity(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event("GET", resource="/users/orders"), None
        )
        assert resp["statusCode"] == 401

    def test_returns_orders_for_authenticated_guest(self, dynamodb_tables, products_table, make_product, make_event):
        import lambda_handler
        make_product(products_table, product_id="p1", price="10.00", qty=5)
        add_resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/cart/items", headers={"X-Guest-Id": "guest-9"},
            body={"product_id": "p1", "quantity": 1},
        ), None)
        order_id = body_of(add_resp)["cart"]["order_id"]
        lambda_handler.lambda_handler(make_event(
            "POST", resource="/cart/{orderId}/checkout", path_params={"orderId": order_id},
            headers={"X-Guest-Id": "guest-9"},
            body={"customer_email": "guest@example.com", "shipping": _shipping()},
        ), None)

        resp = lambda_handler.lambda_handler(make_event(
            "GET", resource="/users/orders", headers={"X-Guest-Id": "guest-9"},
        ), None)
        assert resp["statusCode"] == 200
        orders = body_of(resp)["orders"]
        assert len(orders) == 1
        assert orders[0]["order_id"] == order_id

    def test_list_my_orders_internal_error(self, dynamodb_tables, make_event, monkeypatch):
        import lambda_handler

        class BoomService:
            def list_my_orders(self, user_id):
                raise RuntimeError("boom")

        monkeypatch.setattr(lambda_handler, "_get_service", lambda: BoomService())
        resp = lambda_handler.lambda_handler(make_event(
            "GET", resource="/users/orders", headers={"X-Guest-Id": "guest-1"},
        ), None)
        assert resp["statusCode"] == 500


class TestGetOrderErrors:
    def test_empty_order_id_in_path_is_falsy_so_routes_to_list(self, dynamodb_tables, make_event):
        # bool("") is False, so `has_order_id` treats an empty-string orderId
        # the same as no path param at all — this route can never actually
        # reach _handle_get_order's 400 branch; only PUT/DELETE can (see
        # TestUpdateOrder / TestDeleteOrder below), since those call
        # _parse_order_id() unconditionally rather than gating on has_order_id.
        import lambda_handler
        resp = lambda_handler.lambda_handler(make_event(
            "GET", resource="/orders/{orderId}", path_params={"orderId": ""},
        ), None)
        assert resp["statusCode"] == 200
        assert "pagination" in body_of(resp)

    def test_no_path_params_at_all_treated_as_list(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(make_event(
            "GET", resource="/orders/{orderId}", path_params=None,
        ), None)
        # has_order_id is False -> routes to list, not get
        assert resp["statusCode"] == 200
        assert "pagination" in body_of(resp)

    def test_get_order_internal_error(self, dynamodb_tables, make_event, monkeypatch):
        import lambda_handler

        class BoomService:
            def get_order(self, order_id):
                raise RuntimeError("boom")

        monkeypatch.setattr(lambda_handler, "_get_service", lambda: BoomService())
        resp = lambda_handler.lambda_handler(make_event(
            "GET", resource="/orders/{orderId}", path_params={"orderId": "o1"},
        ), None)
        assert resp["statusCode"] == 500


class TestListOrdersErrors:
    def test_list_orders_internal_error(self, dynamodb_tables, make_event, monkeypatch):
        import lambda_handler

        class BoomService:
            def list_orders(self, **kwargs):
                raise RuntimeError("boom")

        monkeypatch.setattr(lambda_handler, "_get_service", lambda: BoomService())
        resp = lambda_handler.lambda_handler(make_event("GET", resource="/orders"), None)
        assert resp["statusCode"] == 500

    def test_list_orders_query_param_parsing(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(make_event(
            "GET", resource="/orders",
            qs={"page": "not-a-number", "page_size": "9999", "include_deleted": "TRUE", "include_carts": "yes"},
        ), None)
        assert resp["statusCode"] == 200
        body = body_of(resp)
        assert body["pagination"]["page"] == 1  # invalid -> default
        assert body["pagination"]["page_size"] == 200  # clamped to MAX_PAGE_SIZE


class TestCreateOrder:
    def _valid_body(self, **overrides):
        body = {
            "user_id": "u1",
            "customer_email": "benny@example.com",
            "items": [{"product_id": "p1", "quantity": 2}],
            "shipping": _shipping(),
        }
        body.update(overrides)
        return body

    def test_create_order_invalid_json_body(self, dynamodb_tables, make_event):
        import lambda_handler
        event = make_event("POST", resource="/orders")
        event["body"] = "{not json"
        resp = lambda_handler.lambda_handler(event, None)
        assert resp["statusCode"] == 400
        assert "not valid JSON" in body_of(resp)["error"]

    def test_create_order_validation_error_missing_field(self, dynamodb_tables, make_event):
        import lambda_handler
        body = self._valid_body()
        del body["user_id"]
        resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/orders", body=body,
        ), None)
        assert resp["statusCode"] == 422

    def test_create_order_success(self, dynamodb_tables, products_table, make_product, make_event):
        import lambda_handler
        make_product(products_table, product_id="p1", price="10.00", qty=5)
        resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/orders", body=self._valid_body(),
        ), None)
        assert resp["statusCode"] == 201
        body = body_of(resp)
        assert body["order"]["status"] == "pending"
        assert "Order created" in body["message"]

    def test_create_order_business_validation_error(self, dynamodb_tables, products_table, make_event):
        import lambda_handler
        # No product seeded -> service raises ValidationError (product not found)
        resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/orders", body=self._valid_body(),
        ), None)
        assert resp["statusCode"] == 422
        assert "not found" in body_of(resp)["error"]

    def test_create_order_client_error_maps_to_500(self, dynamodb_tables, make_event, monkeypatch):
        import lambda_handler
        from botocore.exceptions import ClientError

        class BoomService:
            def create_order(self, request):
                raise ClientError({"Error": {"Code": "Boom", "Message": "x"}}, "PutItem")

        monkeypatch.setattr(lambda_handler, "_get_service", lambda: BoomService())
        resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/orders", body=self._valid_body(),
        ), None)
        assert resp["statusCode"] == 500

    def test_create_order_unexpected_error_maps_to_500(self, dynamodb_tables, make_event, monkeypatch):
        import lambda_handler

        class BoomService:
            def create_order(self, request):
                raise RuntimeError("kaboom")

        monkeypatch.setattr(lambda_handler, "_get_service", lambda: BoomService())
        resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/orders", body=self._valid_body(),
        ), None)
        assert resp["statusCode"] == 500


class TestUpdateOrder:
    def test_update_invalid_order_id(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(make_event(
            "PUT", resource="/orders/{orderId}", path_params={"orderId": ""},
            body={"customer_notes": "hi"},
        ), None)
        assert resp["statusCode"] == 400

    def test_update_invalid_json_body(self, dynamodb_tables, make_event):
        import lambda_handler
        event = make_event("PUT", resource="/orders/{orderId}", path_params={"orderId": "o1"})
        event["body"] = "{bad"
        resp = lambda_handler.lambda_handler(event, None)
        assert resp["statusCode"] == 400

    def test_update_validation_error_no_fields(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(make_event(
            "PUT", resource="/orders/{orderId}", path_params={"orderId": "o1"}, body={},
        ), None)
        assert resp["statusCode"] == 422

    def test_update_order_not_found(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(make_event(
            "PUT", resource="/orders/{orderId}", path_params={"orderId": "nope"},
            body={"customer_notes": "hello"},
        ), None)
        assert resp["statusCode"] == 404

    def test_update_order_success(self, dynamodb_tables, products_table, make_product, make_event):
        import lambda_handler
        make_product(products_table, product_id="p1", price="10.00", qty=5)
        create_resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/orders", body={
                "user_id": "u1", "customer_email": "benny@example.com",
                "items": [{"product_id": "p1", "quantity": 1}], "shipping": _shipping(),
            },
        ), None)
        order_id = body_of(create_resp)["order"]["order_id"]

        resp = lambda_handler.lambda_handler(make_event(
            "PUT", resource="/orders/{orderId}", path_params={"orderId": order_id},
            body={"customer_notes": "Leave with neighbor"},
        ), None)
        assert resp["statusCode"] == 200
        assert body_of(resp)["order"]["order_id"] == order_id

    def test_update_order_business_validation_error(self, dynamodb_tables, products_table, make_product, make_event):
        import lambda_handler
        make_product(products_table, product_id="p1", price="10.00", qty=5)
        create_resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/orders", body={
                "user_id": "u1", "customer_email": "benny@example.com",
                "items": [{"product_id": "p1", "quantity": 1}], "shipping": _shipping(),
            },
        ), None)
        order_id = body_of(create_resp)["order"]["order_id"]

        resp = lambda_handler.lambda_handler(make_event(
            "PUT", resource="/orders/{orderId}", path_params={"orderId": order_id},
            body={"items": [{"product_id": "no-such-product", "quantity": 1}]},
        ), None)
        assert resp["statusCode"] == 422

    def test_update_order_client_error_maps_to_500(self, dynamodb_tables, make_event, monkeypatch):
        import lambda_handler
        from botocore.exceptions import ClientError

        class BoomService:
            def update_order(self, order_id, update):
                raise ClientError({"Error": {"Code": "Boom", "Message": "x"}}, "UpdateItem")

        monkeypatch.setattr(lambda_handler, "_get_service", lambda: BoomService())
        resp = lambda_handler.lambda_handler(make_event(
            "PUT", resource="/orders/{orderId}", path_params={"orderId": "o1"},
            body={"customer_notes": "x"},
        ), None)
        assert resp["statusCode"] == 500

    def test_update_order_unexpected_error_maps_to_500(self, dynamodb_tables, make_event, monkeypatch):
        import lambda_handler

        class BoomService:
            def update_order(self, order_id, update):
                raise RuntimeError("kaboom")

        monkeypatch.setattr(lambda_handler, "_get_service", lambda: BoomService())
        resp = lambda_handler.lambda_handler(make_event(
            "PUT", resource="/orders/{orderId}", path_params={"orderId": "o1"},
            body={"customer_notes": "x"},
        ), None)
        assert resp["statusCode"] == 500


class TestDeleteOrder:
    def test_delete_invalid_order_id(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(make_event(
            "DELETE", resource="/orders/{orderId}", path_params={"orderId": ""},
        ), None)
        assert resp["statusCode"] == 400

    def test_delete_order_not_found(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(make_event(
            "DELETE", resource="/orders/{orderId}", path_params={"orderId": "nope"},
        ), None)
        assert resp["statusCode"] == 404

    def test_delete_order_success(self, dynamodb_tables, products_table, make_product, make_event):
        import lambda_handler
        make_product(products_table, product_id="p1", price="10.00", qty=5)
        create_resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/orders", body={
                "user_id": "u1", "customer_email": "benny@example.com",
                "items": [{"product_id": "p1", "quantity": 1}], "shipping": _shipping(),
            },
        ), None)
        order_id = body_of(create_resp)["order"]["order_id"]

        resp = lambda_handler.lambda_handler(make_event(
            "DELETE", resource="/orders/{orderId}", path_params={"orderId": order_id},
        ), None)
        assert resp["statusCode"] == 200
        assert body_of(resp)["order_id"] == order_id

        # subsequent GET 404s
        get_resp = lambda_handler.lambda_handler(make_event(
            "GET", resource="/orders/{orderId}", path_params={"orderId": order_id},
        ), None)
        assert get_resp["statusCode"] == 404

    def test_delete_order_internal_error(self, dynamodb_tables, make_event, monkeypatch):
        import lambda_handler

        class BoomService:
            def delete_order(self, order_id):
                raise RuntimeError("boom")

        monkeypatch.setattr(lambda_handler, "_get_service", lambda: BoomService())
        resp = lambda_handler.lambda_handler(make_event(
            "DELETE", resource="/orders/{orderId}", path_params={"orderId": "o1"},
        ), None)
        assert resp["statusCode"] == 500


class TestCartItemErrors:
    def test_add_cart_item_invalid_json(self, dynamodb_tables, make_event):
        import lambda_handler
        event = make_event("POST", resource="/cart/items", headers={"X-Guest-Id": "g1"})
        event["body"] = "{bad"
        resp = lambda_handler.lambda_handler(event, None)
        assert resp["statusCode"] == 400

    def test_add_cart_item_validation_error(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/cart/items", headers={"X-Guest-Id": "g1"},
            body={"product_id": "p1", "quantity": 0},
        ), None)
        assert resp["statusCode"] == 422

    def test_add_cart_item_unknown_product_maps_to_422(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/cart/items", headers={"X-Guest-Id": "g1"},
            body={"product_id": "does-not-exist", "quantity": 1},
        ), None)
        assert resp["statusCode"] == 422

    def test_add_cart_item_internal_error(self, dynamodb_tables, make_event, monkeypatch):
        import lambda_handler

        class BoomService:
            def add_cart_item(self, user_id, product_id, quantity):
                raise RuntimeError("boom")

        monkeypatch.setattr(lambda_handler, "_get_service", lambda: BoomService())
        resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/cart/items", headers={"X-Guest-Id": "g1"},
            body={"product_id": "p1", "quantity": 1},
        ), None)
        assert resp["statusCode"] == 500

    def test_update_cart_item_invalid_product_id(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(make_event(
            "PUT", resource="/cart/items/{productId}", path_params={"productId": ""},
            headers={"X-Guest-Id": "g1"}, body={"quantity": 1},
        ), None)
        assert resp["statusCode"] == 400

    def test_update_cart_item_invalid_json(self, dynamodb_tables, make_event):
        import lambda_handler
        event = make_event(
            "PUT", resource="/cart/items/{productId}", path_params={"productId": "p1"},
            headers={"X-Guest-Id": "g1"},
        )
        event["body"] = "{bad"
        resp = lambda_handler.lambda_handler(event, None)
        assert resp["statusCode"] == 400

    def test_update_cart_item_validation_error_negative_quantity(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(make_event(
            "PUT", resource="/cart/items/{productId}", path_params={"productId": "p1"},
            headers={"X-Guest-Id": "g1"}, body={"quantity": -1},
        ), None)
        assert resp["statusCode"] == 422

    def test_update_cart_item_business_validation_error(self, dynamodb_tables, make_event):
        import lambda_handler
        # empty cart -> service.update_cart_item raises ValidationError
        resp = lambda_handler.lambda_handler(make_event(
            "PUT", resource="/cart/items/{productId}", path_params={"productId": "p1"},
            headers={"X-Guest-Id": "g1"}, body={"quantity": 2},
        ), None)
        assert resp["statusCode"] == 422

    def test_update_cart_item_internal_error(self, dynamodb_tables, make_event, monkeypatch):
        import lambda_handler

        class BoomService:
            def update_cart_item(self, user_id, product_id, quantity):
                raise RuntimeError("boom")

        monkeypatch.setattr(lambda_handler, "_get_service", lambda: BoomService())
        resp = lambda_handler.lambda_handler(make_event(
            "PUT", resource="/cart/items/{productId}", path_params={"productId": "p1"},
            headers={"X-Guest-Id": "g1"}, body={"quantity": 2},
        ), None)
        assert resp["statusCode"] == 500

    def test_remove_cart_item_invalid_product_id(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(make_event(
            "DELETE", resource="/cart/items/{productId}", path_params={"productId": ""},
            headers={"X-Guest-Id": "g1"},
        ), None)
        assert resp["statusCode"] == 400

    def test_remove_cart_item_internal_error(self, dynamodb_tables, make_event, monkeypatch):
        import lambda_handler

        class BoomService:
            def remove_cart_item(self, user_id, product_id):
                raise RuntimeError("boom")

        monkeypatch.setattr(lambda_handler, "_get_service", lambda: BoomService())
        resp = lambda_handler.lambda_handler(make_event(
            "DELETE", resource="/cart/items/{productId}", path_params={"productId": "p1"},
            headers={"X-Guest-Id": "g1"},
        ), None)
        assert resp["statusCode"] == 500


class TestCheckoutCartErrors:
    def test_checkout_invalid_order_id(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/cart/{orderId}/checkout", path_params={"orderId": ""},
            headers={"X-Guest-Id": "g1"},
            body={"customer_email": "x@example.com", "shipping": _shipping()},
        ), None)
        assert resp["statusCode"] == 400

    def test_checkout_invalid_json(self, dynamodb_tables, make_event):
        import lambda_handler
        event = make_event(
            "POST", resource="/cart/{orderId}/checkout", path_params={"orderId": "o1"},
            headers={"X-Guest-Id": "g1"},
        )
        event["body"] = "{bad"
        resp = lambda_handler.lambda_handler(event, None)
        assert resp["statusCode"] == 400

    def test_checkout_validation_error_missing_field(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/cart/{orderId}/checkout", path_params={"orderId": "o1"},
            headers={"X-Guest-Id": "g1"}, body={"shipping": _shipping()},
        ), None)
        assert resp["statusCode"] == 422

    def test_checkout_business_validation_error_cart_not_found(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/cart/{orderId}/checkout", path_params={"orderId": "nonexistent"},
            headers={"X-Guest-Id": "g1"},
            body={"customer_email": "x@example.com", "shipping": _shipping()},
        ), None)
        assert resp["statusCode"] == 422

    def test_checkout_client_error_maps_to_500(self, dynamodb_tables, make_event, monkeypatch):
        import lambda_handler
        from botocore.exceptions import ClientError

        class BoomService:
            def checkout_cart(self, user_id, order_id, request):
                raise ClientError({"Error": {"Code": "Boom", "Message": "x"}}, "UpdateItem")

        monkeypatch.setattr(lambda_handler, "_get_service", lambda: BoomService())
        resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/cart/{orderId}/checkout", path_params={"orderId": "o1"},
            headers={"X-Guest-Id": "g1"},
            body={"customer_email": "x@example.com", "shipping": _shipping()},
        ), None)
        assert resp["statusCode"] == 500

    def test_checkout_unexpected_error_maps_to_500(self, dynamodb_tables, make_event, monkeypatch):
        import lambda_handler

        class BoomService:
            def checkout_cart(self, user_id, order_id, request):
                raise RuntimeError("kaboom")

        monkeypatch.setattr(lambda_handler, "_get_service", lambda: BoomService())
        resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/cart/{orderId}/checkout", path_params={"orderId": "o1"},
            headers={"X-Guest-Id": "g1"},
            body={"customer_email": "x@example.com", "shipping": _shipping()},
        ), None)
        assert resp["statusCode"] == 500


class TestClaimCartErrors:
    def test_claim_invalid_json(self, dynamodb_tables, make_event):
        import lambda_handler
        event = make_event("POST", resource="/cart/claim")
        event["requestContext"] = {"authorizer": {"claims": {"sub": "sub-1"}}}
        event["body"] = "{bad"
        resp = lambda_handler.lambda_handler(event, None)
        assert resp["statusCode"] == 400

    def test_claim_validation_error_missing_guest_id(self, dynamodb_tables, make_event):
        import lambda_handler
        event = make_event("POST", resource="/cart/claim", body={})
        event["requestContext"] = {"authorizer": {"claims": {"sub": "sub-1"}}}
        resp = lambda_handler.lambda_handler(event, None)
        assert resp["statusCode"] == 422

    def test_claim_internal_error(self, dynamodb_tables, make_event, monkeypatch):
        import lambda_handler

        class BoomService:
            def claim_guest_cart(self, authenticated_user_id, guest_id):
                raise RuntimeError("boom")

        monkeypatch.setattr(lambda_handler, "_get_service", lambda: BoomService())
        event = make_event("POST", resource="/cart/claim", body={"guest_id": "g1"})
        event["requestContext"] = {"authorizer": {"claims": {"sub": "sub-1"}}}
        resp = lambda_handler.lambda_handler(event, None)
        assert resp["statusCode"] == 500


class TestGetCartInternalError:
    def test_get_cart_internal_error(self, dynamodb_tables, make_event, monkeypatch):
        import lambda_handler

        class BoomService:
            def get_cart(self, user_id):
                raise RuntimeError("boom")

        monkeypatch.setattr(lambda_handler, "_get_service", lambda: BoomService())
        resp = lambda_handler.lambda_handler(make_event(
            "GET", resource="/cart", headers={"X-Guest-Id": "g1"},
        ), None)
        assert resp["statusCode"] == 500


class TestGetServiceSingleton:
    def test_get_service_lazily_initializes_and_reuses(self, dynamodb_tables, monkeypatch):
        import lambda_handler
        monkeypatch.setattr(lambda_handler, "_service", None)
        first = lambda_handler._get_service()
        second = lambda_handler._get_service()
        assert first is second
