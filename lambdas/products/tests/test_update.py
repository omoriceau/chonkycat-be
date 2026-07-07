from tests.conftest import body_of
from handlers.create import handle_create_product
from handlers.update import handle_update_product


def _create(db, make_event, **overrides):
    body = {"sku": "SKU", "name": "Product"}
    body.update(overrides)
    resp = handle_create_product(db, make_event("POST", body=body))
    assert resp["statusCode"] == 201, resp
    return body_of(resp)["data"]


class TestUpdateProduct:
    def test_partial_update_single_field(self, db, make_event):
        created = _create(db, make_event, sku="UPD-1")
        resp = handle_update_product(db, make_event("PUT", body={"name": "New Name"}), created["id"])
        assert resp["statusCode"] == 200
        data = body_of(resp)["data"]
        assert data["name"] == "New Name"
        assert data["sku"] == "UPD-1"  # untouched fields preserved

    def test_update_ingredients(self, db, make_event):
        created = _create(db, make_event, sku="UPD-2")
        resp = handle_update_product(
            db, make_event("PUT", body={"ingredients": "chicken, rice"}), created["id"]
        )
        assert body_of(resp)["data"]["ingredients"] == "chicken, rice"

    def test_update_nonexistent_product(self, db, make_event):
        resp = handle_update_product(db, make_event("PUT", body={"name": "X"}), "nonexistent")
        assert resp["statusCode"] == 404

    def test_update_blank_product_id(self, db, make_event):
        resp = handle_update_product(db, make_event("PUT", body={"name": "X"}), "")
        assert resp["statusCode"] == 400

    def test_update_empty_body_rejected(self, db, make_event):
        created = _create(db, make_event, sku="UPD-3")
        resp = handle_update_product(db, make_event("PUT", body={}), created["id"])
        assert resp["statusCode"] == 400

    def test_update_invalid_json_rejected(self, db, make_event):
        created = _create(db, make_event, sku="UPD-4")
        event = make_event("PUT", product_id=created["id"])
        event["body"] = "{bad json"
        resp = handle_update_product(db, event, created["id"])
        assert resp["statusCode"] == 400

    def test_update_sku_to_existing_sku_rejected(self, db, make_event):
        _create(db, make_event, sku="TAKEN")
        created2 = _create(db, make_event, sku="UPD-5")
        resp = handle_update_product(db, make_event("PUT", body={"sku": "TAKEN"}), created2["id"])
        assert resp["statusCode"] == 409

    def test_update_sku_to_own_current_value_allowed(self, db, make_event):
        created = _create(db, make_event, sku="UPD-6")
        resp = handle_update_product(db, make_event("PUT", body={"sku": "UPD-6", "name": "Renamed"}), created["id"])
        assert resp["statusCode"] == 200

    def test_update_invalid_price_rejected(self, db, make_event):
        created = _create(db, make_event, sku="UPD-7")
        resp = handle_update_product(db, make_event("PUT", body={"price": "abc"}), created["id"])
        assert resp["statusCode"] == 400

    def test_null_on_not_null_fields_rejected(self, db, make_event):
        created = _create(db, make_event, sku="UPD-8")
        for field in ("sku", "name", "price", "qty", "low_stock_threshold", "active"):
            public_field = "current_stock" if field == "qty" else field
            resp = handle_update_product(db, make_event("PUT", body={public_field: None}), created["id"])
            assert resp["statusCode"] == 400, f"expected 400 nulling {public_field}"

    def test_clearing_nullable_category_does_not_crash(self, db, make_event):
        """Regression: category is a GSI key; clearing it must REMOVE the
        attribute rather than write an explicit NULL."""
        created = _create(db, make_event, sku="UPD-9", category="dry_food")
        resp = handle_update_product(db, make_event("PUT", body={"category": None}), created["id"])
        assert resp["statusCode"] == 200
        assert body_of(resp)["data"]["category"] is None
        raw = db.get_product(created["id"])
        assert "category" not in raw

    def test_clearing_category_with_blank_string(self, db, make_event):
        created = _create(db, make_event, sku="UPD-10", category="dry_food")
        resp = handle_update_product(db, make_event("PUT", body={"category": "  "}), created["id"])
        assert resp["statusCode"] == 200
        assert body_of(resp)["data"]["category"] is None

    def test_reorder_flag_set_when_restocked_below_threshold(self, db, make_event):
        created = _create(db, make_event, sku="UPD-11", current_stock=100, low_stock_threshold=10)
        raw = db.get_product(created["id"])
        assert "reorder_flag" not in raw

        resp = handle_update_product(db, make_event("PUT", body={"current_stock": 2}), created["id"])
        assert body_of(resp)["data"]["is_low_stock"] is True
        raw = db.get_product(created["id"])
        assert raw.get("reorder_flag") == "true"

    def test_reorder_flag_removed_when_restocked_above_threshold(self, db, make_event):
        created = _create(db, make_event, sku="UPD-12", current_stock=1, low_stock_threshold=10)
        raw = db.get_product(created["id"])
        assert raw.get("reorder_flag") == "true"

        resp = handle_update_product(db, make_event("PUT", body={"current_stock": 50}), created["id"])
        assert body_of(resp)["data"]["is_low_stock"] is False
        raw = db.get_product(created["id"])
        assert "reorder_flag" not in raw

    def test_reorder_flag_recomputed_from_threshold_change_alone(self, db, make_event):
        created = _create(db, make_event, sku="UPD-13", current_stock=8, low_stock_threshold=5)
        raw = db.get_product(created["id"])
        assert "reorder_flag" not in raw

        # lowering the stock number isn't required — raising the threshold
        # above current stock should also trigger the flag
        resp = handle_update_product(db, make_event("PUT", body={"low_stock_threshold": 20}), created["id"])
        assert body_of(resp)["data"]["is_low_stock"] is True

    def test_set_deleted_at_to_non_null_rejected(self, db, make_event):
        created = _create(db, make_event, sku="UPD-14")
        resp = handle_update_product(
            db, make_event("PUT", body={"deleted_at": "2026-01-01T00:00:00"}), created["id"]
        )
        assert resp["statusCode"] == 400

    def test_updated_at_changes_on_update(self, db, make_event):
        created = _create(db, make_event, sku="UPD-15")
        resp = handle_update_product(db, make_event("PUT", body={"name": "Changed"}), created["id"])
        data = body_of(resp)["data"]
        assert data["updated_at"] != created["created_at"] or data["updated_at"] >= created["created_at"]
        assert data["created_at"] == created["created_at"]  # created_at never changes


class TestRestoreProduct:
    """Restoring a soft-deleted product happens through the update handler:
    PUT {"deleted_at": null}."""

    def test_restore_soft_deleted_product(self, db, make_event):
        from handlers.delete import handle_delete_product
        from handlers.read import handle_get_product

        created = _create(db, make_event, sku="RES-1")
        handle_delete_product(db, created["id"])
        assert handle_get_product(db, created["id"])["statusCode"] == 404

        resp = handle_update_product(db, make_event("PUT", body={"deleted_at": None}), created["id"])
        assert resp["statusCode"] == 200
        assert body_of(resp)["data"]["deleted_at"] is None

        assert handle_get_product(db, created["id"])["statusCode"] == 200

    def test_restore_can_be_combined_with_other_field_updates(self, db, make_event):
        from handlers.delete import handle_delete_product

        created = _create(db, make_event, sku="RES-2")
        handle_delete_product(db, created["id"])

        resp = handle_update_product(
            db, make_event("PUT", body={"deleted_at": None, "name": "Restored Name"}), created["id"]
        )
        assert resp["statusCode"] == 200
        data = body_of(resp)["data"]
        assert data["deleted_at"] is None
        assert data["name"] == "Restored Name"

    def test_restore_a_never_deleted_product_is_a_no_op(self, db, make_event):
        """Sending deleted_at: null on a product that was never deleted
        shouldn't error — there's just nothing to remove."""
        created = _create(db, make_event, sku="RES-3")
        resp = handle_update_product(db, make_event("PUT", body={"deleted_at": None}), created["id"])
        assert resp["statusCode"] == 200
        assert body_of(resp)["data"]["deleted_at"] is None
