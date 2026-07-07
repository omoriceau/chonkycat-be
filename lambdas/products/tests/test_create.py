import json
from decimal import Decimal

from tests.conftest import body_of
from handlers.create import handle_create_product


class TestCreateProduct:
    def test_create_with_all_fields(self, db, make_event):
        event = make_event("POST", body={
            "sku": "CHK-001",
            "name": "Chonky Salmon Dry Food",
            "description": "Grain-free salmon recipe",
            "ingredients": "salmon, sweet potato, taurine",
            "image_url": "https://cdn.example.com/salmon.png",
            "category": "dry_food",
            "price": 24.99,
            "current_stock": 40,
            "low_stock_threshold": 5,
            "active": True,
        })
        resp = handle_create_product(db, event)
        assert resp["statusCode"] == 201
        data = body_of(resp)["data"]
        assert data["sku"] == "CHK-001"
        assert data["ingredients"] == "salmon, sweet potato, taurine"
        assert data["current_stock"] == 40
        assert data["is_low_stock"] is False
        assert data["id"]  # ULID assigned
        assert data["created_at"] == data["updated_at"]

    def test_create_applies_schema_defaults(self, db, make_event):
        """Matches the original SQL schema: only sku/name are required;
        price defaults to 0.00, qty to 0, low_stock_threshold to 10,
        active to true, category/description/ingredients/image_url absent."""
        event = make_event("POST", body={"sku": "CHK-002", "name": "Minimal Product"})
        resp = handle_create_product(db, event)
        assert resp["statusCode"] == 201
        data = body_of(resp)["data"]
        assert data["price"] == 0
        assert data["current_stock"] == 0
        assert data["low_stock_threshold"] == 10
        assert data["active"] is True
        assert data["category"] is None
        assert data["is_low_stock"] is True  # 0 <= 10

    def test_missing_required_fields(self, db, make_event):
        resp = handle_create_product(db, make_event("POST", body={"name": "No SKU"}))
        assert resp["statusCode"] == 400
        assert "sku" in body_of(resp)["error"]

    def test_blank_sku_rejected(self, db, make_event):
        resp = handle_create_product(db, make_event("POST", body={"sku": "   ", "name": "X"}))
        assert resp["statusCode"] == 400

    def test_blank_name_rejected(self, db, make_event):
        resp = handle_create_product(db, make_event("POST", body={"sku": "SKU1", "name": "  "}))
        assert resp["statusCode"] == 400

    def test_duplicate_sku_rejected(self, db, make_event):
        handle_create_product(db, make_event("POST", body={"sku": "DUPE", "name": "First"}))
        resp = handle_create_product(db, make_event("POST", body={"sku": "DUPE", "name": "Second"}))
        assert resp["statusCode"] == 409

    def test_invalid_price_rejected(self, db, make_event):
        resp = handle_create_product(db, make_event("POST", body={
            "sku": "SKU2", "name": "X", "price": "not-a-number",
        }))
        assert resp["statusCode"] == 400
        assert "price" in body_of(resp)["error"]

    def test_invalid_json_body_rejected(self, db, make_event):
        event = make_event("POST")
        event["body"] = "{not valid json"
        resp = handle_create_product(db, event)
        assert resp["statusCode"] == 400

    def test_low_stock_flag_set_on_create_when_at_threshold(self, db, make_event):
        resp = handle_create_product(db, make_event("POST", body={
            "sku": "SKU3", "name": "X", "current_stock": 5, "low_stock_threshold": 5,
        }))
        data = body_of(resp)["data"]
        assert data["is_low_stock"] is True

        # verify the raw item actually has the sparse reorder_flag attribute set,
        # not just that the serialized response computes it correctly
        raw = db.get_product(data["id"])
        assert raw.get("reorder_flag") == "true"

    def test_reorder_flag_absent_when_stock_healthy(self, db, make_event):
        resp = handle_create_product(db, make_event("POST", body={
            "sku": "SKU4", "name": "X", "current_stock": 100, "low_stock_threshold": 5,
        }))
        data = body_of(resp)["data"]
        raw = db.get_product(data["id"])
        assert "reorder_flag" not in raw

    def test_category_omitted_does_not_crash_gsi(self, db, make_event):
        """Regression test: category is a GSI hash key, so an explicit
        None must never be written — it must be entirely absent."""
        resp = handle_create_product(db, make_event("POST", body={"sku": "SKU5", "name": "X"}))
        assert resp["statusCode"] == 201
        raw = db.get_product(body_of(resp)["data"]["id"])
        assert "category" not in raw

    def test_price_stored_as_decimal(self, db, make_event):
        resp = handle_create_product(db, make_event("POST", body={
            "sku": "SKU6", "name": "X", "price": 19.99,
        }))
        raw = db.get_product(body_of(resp)["data"]["id"])
        assert isinstance(raw["price"], Decimal)
        assert raw["price"] == Decimal("19.99")
