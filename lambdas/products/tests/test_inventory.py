from tests.conftest import body_of
from handlers.create import handle_create_product
from handlers.delete import handle_delete_product
from handlers.inventory import handle_check_inventory


def _create(db, make_event, **overrides):
    body = {"sku": "SKU", "name": "Product", "current_stock": 10}
    body.update(overrides)
    resp = handle_create_product(db, make_event("POST", body=body))
    assert resp["statusCode"] == 201, resp
    return body_of(resp)["data"]


def _json_event(items: list) -> dict:
    import json
    return {"httpMethod": "POST", "body": json.dumps(items)}


class TestCheckInventory:
    def test_sufficient_stock_returns_empty_list(self, db, make_event):
        _create(db, make_event, sku="INV-1", current_stock=10)
        resp = handle_check_inventory(db, _json_event([{"sku": "INV-1", "quantity": 5}]))
        assert resp["statusCode"] == 200
        assert body_of(resp) == []

    def test_exact_stock_match_is_sufficient(self, db, make_event):
        _create(db, make_event, sku="INV-2", current_stock=5)
        resp = handle_check_inventory(db, _json_event([{"sku": "INV-2", "quantity": 5}]))
        assert body_of(resp) == []

    def test_insufficient_stock_returns_sku(self, db, make_event):
        _create(db, make_event, sku="INV-3", current_stock=2)
        resp = handle_check_inventory(db, _json_event([{"sku": "INV-3", "quantity": 5}]))
        assert resp["statusCode"] == 200
        assert body_of(resp) == ["INV-3"]

    def test_unknown_sku_returns_sku(self, db):
        resp = handle_check_inventory(db, _json_event([{"sku": "NOPE", "quantity": 1}]))
        assert body_of(resp) == ["NOPE"]

    def test_soft_deleted_sku_returns_sku(self, db, make_event):
        created = _create(db, make_event, sku="INV-4", current_stock=10)
        handle_delete_product(db, created["id"])
        resp = handle_check_inventory(db, _json_event([{"sku": "INV-4", "quantity": 1}]))
        assert body_of(resp) == ["INV-4"]

    def test_mixed_skus_only_returns_insufficient_ones(self, db, make_event):
        _create(db, make_event, sku="INV-5", current_stock=10)
        _create(db, make_event, sku="INV-6", current_stock=1)
        resp = handle_check_inventory(
            db,
            _json_event(
                [{"sku": "INV-5", "quantity": 3}, {"sku": "INV-6", "quantity": 5}]
            ),
        )
        assert body_of(resp) == ["INV-6"]

    def test_empty_body_is_bad_request(self, db):
        resp = handle_check_inventory(db, _json_event([]))
        assert resp["statusCode"] == 400

    def test_non_numeric_qty_treated_as_insufficient(self, db, make_event):
        _create(db, make_event, sku="INV-7", current_stock=10)
        resp = handle_check_inventory(
            db, _json_event([{"sku": "INV-7", "quantity": "notanumber"}])
        )
        assert body_of(resp) == ["INV-7"]

    def test_base64_encoded_json_body(self, db, make_event):
        import base64
        import json
        _create(db, make_event, sku="INV-8", current_stock=1)
        raw = base64.b64encode(
            json.dumps([{"sku": "INV-8", "quantity": 5}]).encode("ascii")
        ).decode("ascii")
        resp = handle_check_inventory(
            db, {"httpMethod": "POST", "body": raw, "isBase64Encoded": True}
        )
        assert body_of(resp) == ["INV-8"]

    def test_item_missing_sku_is_bad_request(self, db):
        resp = handle_check_inventory(db, _json_event([{"quantity": 1}]))
        assert resp["statusCode"] == 400
