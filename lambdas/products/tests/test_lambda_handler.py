from tests.conftest import body_of


class TestRouting:
    """These test the router itself — that each HTTP method/shape reaches
    the right handler — not the handler logic (covered in the other test
    files). db_client creation is exercised for real against the mocked
    table via the `dynamodb_table` fixture."""

    def test_post_routes_to_create(self, dynamodb_table, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event("POST", body={"sku": "RT-1", "name": "X"}), None
        )
        assert resp["statusCode"] == 201

    def test_get_list_routes_to_list(self, dynamodb_table, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(make_event("GET"), None)
        assert resp["statusCode"] == 200
        assert "pagination" in body_of(resp)

    def test_get_single_routes_to_get(self, dynamodb_table, make_event):
        import lambda_handler
        created = body_of(lambda_handler.lambda_handler(
            make_event("POST", body={"sku": "RT-2", "name": "X"}), None
        ))["data"]
        resp = lambda_handler.lambda_handler(make_event("GET", product_id=created["id"]), None)
        assert resp["statusCode"] == 200
        assert body_of(resp)["data"]["id"] == created["id"]

    def test_put_routes_to_update(self, dynamodb_table, make_event):
        import lambda_handler
        created = body_of(lambda_handler.lambda_handler(
            make_event("POST", body={"sku": "RT-3", "name": "X"}), None
        ))["data"]
        resp = lambda_handler.lambda_handler(
            make_event("PUT", product_id=created["id"], body={"name": "Renamed"}), None
        )
        assert resp["statusCode"] == 200
        assert body_of(resp)["data"]["name"] == "Renamed"

    def test_patch_also_routes_to_update(self, dynamodb_table, make_event):
        import lambda_handler
        created = body_of(lambda_handler.lambda_handler(
            make_event("POST", body={"sku": "RT-4", "name": "X"}), None
        ))["data"]
        resp = lambda_handler.lambda_handler(
            make_event("PATCH", product_id=created["id"], body={"name": "Patched"}), None
        )
        assert resp["statusCode"] == 200
        assert body_of(resp)["data"]["name"] == "Patched"

    def test_delete_routes_to_delete(self, dynamodb_table, make_event):
        import lambda_handler
        created = body_of(lambda_handler.lambda_handler(
            make_event("POST", body={"sku": "RT-5", "name": "X"}), None
        ))["data"]
        resp = lambda_handler.lambda_handler(make_event("DELETE", product_id=created["id"]), None)
        assert resp["statusCode"] == 204

    def test_http_api_v2_event_shape_routes_correctly(self, dynamodb_table, make_event_v2):
        """HTTP API (payload format 2.0) nests the method under
        requestContext.http.method instead of a top-level httpMethod."""
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event_v2("POST", body={"sku": "RT-6", "name": "X"}), None
        )
        assert resp["statusCode"] == 201

        resp = lambda_handler.lambda_handler(make_event_v2("GET"), None)
        assert resp["statusCode"] == 200

    def test_get_single_with_show_deleted_query_param(self, dynamodb_table, make_event):
        import lambda_handler
        from handlers.delete import handle_delete_product
        import db as db_module

        created = body_of(lambda_handler.lambda_handler(
            make_event("POST", body={"sku": "RT-7", "name": "X"}), None
        ))["data"]
        handle_delete_product(db_module.get_db_client(), created["id"])

        resp = lambda_handler.lambda_handler(make_event("GET", product_id=created["id"]), None)
        assert resp["statusCode"] == 404

        resp = lambda_handler.lambda_handler(
            make_event("GET", product_id=created["id"], qs={"show_deleted": "true"}), None
        )
        assert resp["statusCode"] == 200

    def test_post_inventory_check_routes_to_check_inventory(self, dynamodb_table, make_event):
        import lambda_handler
        body_of(lambda_handler.lambda_handler(
            make_event("POST", body={"sku": "RT-8", "name": "X", "current_stock": 10}), None
        ))

        event = {
            "httpMethod": "POST",
            "resource": "/products/inventory-check",
            "path": "/products/inventory-check",
            "body": "RT-8=1",
        }
        resp = lambda_handler.lambda_handler(event, None)
        assert resp["statusCode"] == 200
        assert body_of(resp)["data"] == []

    def test_missing_table_env_var_returns_500(self, monkeypatch, make_event):
        import importlib
        import lambda_handler
        monkeypatch.delenv("PRODUCTS_TABLE_NAME", raising=False)
        resp = lambda_handler.lambda_handler(make_event("GET"), None)
        assert resp["statusCode"] == 500
