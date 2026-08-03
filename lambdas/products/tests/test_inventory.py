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


def _form_event(form_body: str) -> dict:
    return {"httpMethod": "POST", "body": form_body}


class TestCheckInventory:
    def test_sufficient_stock_returns_empty_list(self, db, make_event):
        _create(db, make_event, sku="INV-1", current_stock=10)
        resp = handle_check_inventory(db, _form_event("INV-1=5"))
        assert resp["statusCode"] == 200
        assert body_of(resp)["data"] == []

    def test_exact_stock_match_is_sufficient(self, db, make_event):
        _create(db, make_event, sku="INV-2", current_stock=5)
        resp = handle_check_inventory(db, _form_event("INV-2=5"))
        assert body_of(resp)["data"] == []

    def test_insufficient_stock_returns_sku(self, db, make_event):
        _create(db, make_event, sku="INV-3", current_stock=2)
        resp = handle_check_inventory(db, _form_event("INV-3=5"))
        assert resp["statusCode"] == 200
        assert body_of(resp)["data"] == ["INV-3"]

    def test_unknown_sku_returns_sku(self, db):
        resp = handle_check_inventory(db, _form_event("NOPE=1"))
        assert body_of(resp)["data"] == ["NOPE"]

    def test_soft_deleted_sku_returns_sku(self, db, make_event):
        created = _create(db, make_event, sku="INV-4", current_stock=10)
        handle_delete_product(db, created["id"])
        resp = handle_check_inventory(db, _form_event("INV-4=1"))
        assert body_of(resp)["data"] == ["INV-4"]

    def test_mixed_skus_only_returns_insufficient_ones(self, db, make_event):
        _create(db, make_event, sku="INV-5", current_stock=10)
        _create(db, make_event, sku="INV-6", current_stock=1)
        resp = handle_check_inventory(db, _form_event("INV-5=3&INV-6=5"))
        assert body_of(resp)["data"] == ["INV-6"]

    def test_empty_body_is_bad_request(self, db):
        resp = handle_check_inventory(db, _form_event(""))
        assert resp["statusCode"] == 400

    def test_non_numeric_qty_treated_as_insufficient(self, db, make_event):
        _create(db, make_event, sku="INV-7", current_stock=10)
        resp = handle_check_inventory(db, _form_event("INV-7=notanumber"))
        assert body_of(resp)["data"] == ["INV-7"]

    def test_base64_encoded_form_body(self, db, make_event):
        import base64
        _create(db, make_event, sku="INV-8", current_stock=1)
        raw = base64.b64encode(b"INV-8=5").decode("ascii")
        resp = handle_check_inventory(
            db, {"httpMethod": "POST", "body": raw, "isBase64Encoded": True}
        )
        assert body_of(resp)["data"] == ["INV-8"]
