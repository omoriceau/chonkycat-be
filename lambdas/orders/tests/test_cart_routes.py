from tests.conftest import body_of


class TestCartRouting:
    """Exercises lambda_handler's /cart* dispatch and identity resolution
    end-to-end against the mocked tables — not just the service layer."""

    def test_get_cart_requires_identity(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event("GET", resource="/cart"), None
        )
        assert resp["statusCode"] == 401

    def test_get_empty_cart_as_guest(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event("GET", resource="/cart", headers={"X-Guest-Id": "guest-1"}), None
        )
        assert resp["statusCode"] == 200
        assert body_of(resp)["cart"]["items"] == []

    def test_add_item_then_get_cart_as_guest(self, dynamodb_tables, products_table, make_product, make_event):
        import lambda_handler
        make_product(products_table, product_id="p1", price="12.50")

        add_resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/cart/items",
            headers={"X-Guest-Id": "guest-1"},
            body={"product_id": "p1", "quantity": 3},
        ), None)
        assert add_resp["statusCode"] == 201
        assert body_of(add_resp)["cart"]["subtotal"] == "37.50"

        get_resp = lambda_handler.lambda_handler(make_event(
            "GET", resource="/cart", headers={"X-Guest-Id": "guest-1"},
        ), None)
        assert body_of(get_resp)["cart"]["subtotal"] == "37.50"

    def test_update_and_remove_cart_item(self, dynamodb_tables, products_table, make_product, make_event):
        import lambda_handler
        make_product(products_table, product_id="p1", price="10.00")
        lambda_handler.lambda_handler(make_event(
            "POST", resource="/cart/items", headers={"X-Guest-Id": "guest-1"},
            body={"product_id": "p1", "quantity": 1},
        ), None)

        update_resp = lambda_handler.lambda_handler(make_event(
            "PUT", resource="/cart/items/{productId}", path_params={"productId": "p1"},
            headers={"X-Guest-Id": "guest-1"}, body={"quantity": 4},
        ), None)
        assert update_resp["statusCode"] == 200
        assert body_of(update_resp)["cart"]["items"][0]["quantity"] == 4

        remove_resp = lambda_handler.lambda_handler(make_event(
            "DELETE", resource="/cart/items/{productId}", path_params={"productId": "p1"},
            headers={"X-Guest-Id": "guest-1"},
        ), None)
        assert remove_resp["statusCode"] == 200
        assert body_of(remove_resp)["cart"]["items"] == []

    def test_checkout_route(self, dynamodb_tables, products_table, make_product, make_event):
        import lambda_handler
        make_product(products_table, product_id="p1", price="10.00", qty=5)
        add_resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/cart/items", headers={"X-Guest-Id": "guest-1"},
            body={"product_id": "p1", "quantity": 2},
        ), None)
        order_id = body_of(add_resp)["cart"]["order_id"]

        checkout_resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/cart/{orderId}/checkout", path_params={"orderId": order_id},
            headers={"X-Guest-Id": "guest-1"},
            body={
                "customer_email": "guest@example.com",
                "shipping": {
                    "name": "Guest Shopper", "address1": "1 Main St", "city": "Toronto",
                    "province": "ON", "postal_code": "M5V 2H1", "country": "Canada",
                },
            },
        ), None)
        assert checkout_resp["statusCode"] == 201
        assert body_of(checkout_resp)["order"]["status"] == "pending"

    def test_claim_requires_cognito_authorizer_claims(self, dynamodb_tables, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(make_event(
            "POST", resource="/cart/claim", body={"guest_id": "guest-1"},
        ), None)
        assert resp["statusCode"] == 401

    def test_claim_reassigns_guest_cart_to_authenticated_user(
        self, dynamodb_tables, products_table, make_product, make_event
    ):
        import lambda_handler
        make_product(products_table, product_id="p1", price="10.00")
        lambda_handler.lambda_handler(make_event(
            "POST", resource="/cart/items", headers={"X-Guest-Id": "guest-1"},
            body={"product_id": "p1", "quantity": 2},
        ), None)

        event = make_event("POST", resource="/cart/claim", body={"guest_id": "guest-1"})
        event["requestContext"] = {"authorizer": {"claims": {"sub": "cognito-sub-1"}}}
        resp = lambda_handler.lambda_handler(event, None)

        assert resp["statusCode"] == 200
        cart = body_of(resp)["cart"]
        assert cart["items"][0]["quantity"] == 2

        # The guest identity no longer has an open cart — it was re-keyed.
        guest_get = lambda_handler.lambda_handler(make_event(
            "GET", resource="/cart", headers={"X-Guest-Id": "guest-1"},
        ), None)
        assert body_of(guest_get)["cart"]["items"] == []
